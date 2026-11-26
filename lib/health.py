#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""健康检查：把服务、数据、节目单覆盖、任务计划、配置与连通性逐项核对。

每一项都给出三样东西：结果数值、状态、这项是什么意思（给不看代码的人读的说明）。
状态只有五种：
    ok    正常
    warn  需要注意：现在还能用，但放着不管会出问题
    fail  异常：已经影响使用
    info  仅展示数值，没有好坏之分
    off   功能未启用

原则：
- 只读本地文件并在内存里统计，不修改任何东西。
- 除了对内网的组播代理 / 认证服务器各做一次 TCP 连接探测外，不发起任何请求。
- 机顶盒凭据只判断「填没填」，不读取也不展示具体值。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import urlparse

from .logo import IMG_EXT

# 抓取脚本真正会强制校验的机顶盒字段（见 get_iptv_channels_V1.1.py 的 REQUIRED）；
# 其余字段留空也能跑，脚本里只是取默认值。
FETCH_REQUIRED = ("AuthenticationIP", "UserID", "mac", "STBID", "Authenticator")
PROXY_KEYS = (("udpxy", "组播代理 udpxy"), ("rtp2httpd", "组播代理 rtp2httpd"))
TIME_FMT = "%Y-%m-%d %H:%M:%S"

_DAY_RE = re.compile(rb'\bstart="(\d{8})')
_PROG_RE = re.compile(rb'<programme\b[^>]*\bchannel="([^"]*)"')
# 单条 <programme …> 标签最长不过两百来字节；分块读取时，块尾这么长的范围留给下一块匹配
_TAIL = 512
_CHUNK = 4 << 20
_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?$")
_CACHE: dict = {}


# ---------------------------------------------------------------- 格式化
def _size_text(n: float) -> str:
    for unit, scale in (("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)):
        if abs(n) >= scale:
            return "%.1f %s" % (n / float(scale), unit)
    return "%d B" % n


def _age_text(ts: float, now: float) -> str:
    """「3 小时前」这样的相对时间。"""
    d = max(0, now - ts)
    if d < 60:
        return "刚刚"
    if d < 3600:
        return "%d 分钟前" % (d // 60)
    if d < 86400:
        return "%.1f 小时前" % (d / 3600.0)
    return "%.1f 天前" % (d / 86400.0)


def _ts_text(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "-"


def _md(d: datetime) -> str:
    return d.strftime("%m-%d")


def _elapsed_text(seconds: float) -> str:
    """一段时长的中文写法。"""
    seconds = int(max(0, seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return "%d 天 %d 小时 %d 分钟" % (days, hours, minutes)
    if hours:
        return "%d 小时 %d 分钟" % (hours, minutes)
    return "%d 分钟" % minutes if minutes else "%d 秒" % seconds


def _to_ts(text: str) -> float:
    try:
        return datetime.strptime(text, TIME_FMT).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _rel(root: str, path: str) -> str:
    try:
        return os.path.relpath(path, root).replace("\\", "/")
    except ValueError:
        return path


def _trigger_text(trigger) -> str:
    return {"schedule": "定时", "startup": "启动时自动", "manual": "手动"}.get(trigger or "", "手动")


def _platform_text() -> str:
    return {"win32": "Windows", "linux": "Linux", "darwin": "macOS"}.get(sys.platform, sys.platform)


def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _cached(kind: str, path: str, compute):
    """按 (类型, 路径, 修改时间, 大小) 缓存一次扫描结果；文件没变就不重扫。"""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (kind, path, st.st_mtime_ns, st.st_size)
    if key not in _CACHE:
        if len(_CACHE) > 16:
            _CACHE.clear()
        _CACHE[key] = compute(path)
    return _CACHE[key]


def _dir_size(path: str) -> int:
    total = 0
    for base, _dirs, files in os.walk(path):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(base, fn))
            except OSError:
                pass
    return total


def _img_count(path: str) -> int:
    try:
        return sum(1 for fn in os.listdir(path) if os.path.splitext(fn)[1].lower() in IMG_EXT)
    except OSError:
        return 0


# ---------------------------------------------------------------- 读取
def count_channels(path: str) -> int:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return sum(1 for ln in f if ln.startswith("#EXTINF"))
    except OSError:
        return 0


def _scan_epg(path: str) -> dict:
    """统计 XMLTV：每个日期（YYYYMMDD）各有多少条节目，以及哪些频道至少有一条节目。

    文件有十来 MB，所以按块读。匹配在整块上进行，但只采纳「起点距块尾
    不少于 _TAIL」的匹配，其余的留给下一块：每块开头的 _TAIL 字节就是上
    一块的尾巴，一条完整的匹配最长不过两百来字节，这样每条节目恰好被数一次。
    """
    days: Counter = Counter()
    with_ids: set = set()

    def take(buf: bytes, limit: int | None):
        # limit 之前开始的匹配归本块；limit 为 None 表示已到文件尾，全部采纳
        for m in _PROG_RE.finditer(buf):
            if limit is None or m.start() < limit:
                with_ids.add(m.group(1).decode("utf-8", "replace"))
        for m in _DAY_RE.finditer(buf):
            if limit is None or m.start() < limit:
                days[m.group(1)] += 1

    tail = b""
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            buf = tail + chunk
            if len(buf) > _TAIL:
                take(buf, len(buf) - _TAIL)
                tail = buf[-_TAIL:]
            else:
                tail = buf
    take(tail, None)
    return {"days": {k.decode("ascii", "replace"): v for k, v in days.items()}, "ids": with_ids}


def epg_day_counts(path: str) -> dict:
    return (_cached("epg", path, _scan_epg) or {}).get("days", {})


def epg_programme_channels(path: str) -> set:
    """节目单里至少有一条节目的频道 id。

    注意不能用 <channel id="…"> 来数：整理层会给播放列表里的每个频道都建
    条目，哪怕这个频道一条节目都没有。
    """
    return (_cached("epg", path, _scan_epg) or {}).get("ids", set())


def _range_text(counts: dict) -> tuple[str, int]:
    if not counts:
        return "没有任何节目", 0
    days = sorted(counts)
    return "%s ~ %s，共 %d 天" % (days[0][4:6] + "-" + days[0][6:8],
                                 days[-1][4:6] + "-" + days[-1][6:8], len(days)), len(days)


def tcp_reachable(url: str, timeout: float = 2.0) -> bool | None:
    """对内网地址做一次 TCP 连接探测。地址为空或写得不合法时返回 None。

    主机名先做格式校验：占位符之类的写法直接判为不合法，不去做域名解析
    （解析失败要等好几秒，页面会卡住）。写成 ``192.168.1.9:4022`` 这种不带
    http:// 的也能识别，省得用户因为少写协议头看到「格式不对」。
    """
    raw = str(url or "").strip()
    try:
        u = urlparse(raw if "://" in raw else "//" + raw.lstrip("/"), scheme="http")
        host, port = u.hostname, u.port
    except ValueError:
        return None
    if not host or not _HOST_RE.match(host):
        return None
    port = port or (443 if u.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _probe_text(ok: bool | None) -> tuple[str, str]:
    """把探测结果翻译成 (显示文字, 状态)。"""
    if ok is True:
        return "连接正常", "ok"
    if ok is False:
        return "连不上", "fail"
    return "地址格式不对", "warn"


# ---------------------------------------------------------------- 报告
def build(ctx: dict, detailed: bool = True) -> dict:
    """生成报告。detailed=False 时只核对「服务是否在运行」（给未登录访问者）。

    ctx 的键：root, state, started_at, source_playlist, source_epg, playlists_dir,
    output_dir, logo_dir, user_logo_dir, config_path, organize_cfg, schedule,
    proxy, protect_media, next_run, groups_version_default
    """
    now = time.time()
    today = datetime.now()
    today_key = today.strftime("%Y%m%d")
    root = ctx.get("root") or "."
    sections: list[dict] = []
    tally: Counter = Counter()

    def row(name, value, status, desc=""):
        tally[status] += 1
        return {"name": name, "value": value, "status": status, "desc": desc}

    def section(title, intro, rows):
        if rows:
            sections.append({"title": title, "intro": intro, "rows": rows})

    # ------------------------------------------------------------ 服务
    state = ctx.get("state") or {}
    started = ctx.get("started_at") or now
    rows = [
        row("服务状态", "运行中", "ok", "本页面由服务实时生成，能打开就说明 Web 服务在工作。"),
        row("当前时间", datetime.fromtimestamp(now).strftime(TIME_FMT), "info"),
    ]
    if not detailed:
        section("服务", "", rows)
        return _finish(sections, tally, now)

    rows.append(row(
        "运行时长", _elapsed_text(now - started), "info",
        "进程启动于 %s，期间没有重启过。" % datetime.fromtimestamp(started).strftime(TIME_FMT)))
    rows.append(row(
        "运行环境", "Python %d.%d · %s" % (sys.version_info[0], sys.version_info[1], _platform_text()),
        "info", "容器里显示 Linux，源码运行时显示 Windows 或 macOS。"))
    section("服务", "Web 服务本身的状态。", rows)

    # ------------------------------------------------------------ 数据
    playlists_dir = ctx.get("playlists_dir") or ""
    manifest = _load_json(os.path.join(playlists_dir, "manifest.json"))
    rows = []

    src_m3u = ctx.get("source_playlist") or ""
    mt_m3u = os.path.getmtime(src_m3u) if os.path.isfile(src_m3u) else 0.0
    if mt_m3u:
        n = count_channels(src_m3u)
        st, why = "ok", ""
        if n == 0:
            st, why = "fail", "文件是空的，播放列表里将没有任何频道，请重新「抓取并整理」。"
        elif now - mt_m3u > 7 * 86400:
            st, why = "warn", "超过 7 天没有更新：运营商新增或下线的频道不会体现在这里。"
        rows.append(row(
            "源频道表", "%d 个频道 · 更新于 %s（%s）" % (n, _ts_text(mt_m3u), _age_text(mt_m3u, now)), st,
            why or "抓取层从内网抓到的原始频道表（%s）。「立即整理」就是读它来生成播放列表。" % _rel(root, src_m3u)))
    else:
        rows.append(row("源频道表", "文件不存在", "fail", "还没有抓取过。请先填好「机顶盒配置」，再点「抓取并整理」。"))

    src_epg = ctx.get("source_epg") or ""
    mt_epg = os.path.getmtime(src_epg) if os.path.isfile(src_epg) else 0.0
    if mt_epg:
        rng, _ = _range_text(epg_day_counts(src_epg))
        st = "warn" if now - mt_epg > 36 * 3600 else "ok"
        rows.append(row(
            "源节目单", "%s · %s · 更新于 %s（%s）" % (
                _size_text(os.path.getsize(src_epg)), rng, _ts_text(mt_epg), _age_text(mt_epg, now)), st,
            "抓取层抓回来的原始节目单（%s）。%s" % (
                _rel(root, src_epg), "超过 36 小时没更新，今天的节目可能不全。" if st == "warn" else "")))
    else:
        rows.append(row("源节目单", "文件不存在", "warn", "还没有抓取过节目单，播放器将看不到节目预告。"))

    out_epg = os.path.join(playlists_dir, "epg.xml")
    if manifest.get("generated_at"):
        total_c = manifest.get("channel_count") or 0
        generated = manifest["generated_at"]
        stale = mt_m3u > _to_ts(generated)
        rows.append(row(
            "上次整理", "%s · %d 个频道 · %d 个播放列表" % (
                generated, total_c, len(manifest.get("playlists") or [])), "warn" if stale else "ok",
            "源数据在上次整理之后又更新过，播放列表可能不是最新的，点「立即整理」更新。" if stale else
            "分组：" + "、".join("%s %d" % (g, n) for g, n in (manifest.get("group_counts") or {}).items()) + "。"))

        files = manifest.get("files") or []
        missing = [fn for fn in files if not os.path.isfile(os.path.join(playlists_dir, fn))]
        total = sum(os.path.getsize(os.path.join(playlists_dir, fn)) for fn in files
                    if os.path.isfile(os.path.join(playlists_dir, fn)))
        rows.append(row(
            "播放列表文件", "%d 个文件 · %s" % (len(files), _size_text(total)), "fail" if missing else "ok",
            ("缺少：" + "、".join(missing) + "。点「立即整理」重新生成。") if missing
            else "全部在 %s 里，订阅中心列出的就是这些文件。" % _rel(root, playlists_dir)))

        hit = manifest.get("logo_hit") or 0
        built, uploaded = _img_count(ctx.get("logo_dir") or ""), _img_count(ctx.get("user_logo_dir") or "")
        rows.append(row(
            "台标", "命中 %d / %d（%d%%）· 台标库 %d + 已上传 %d" % (
                hit, total_c, round(100.0 * hit / total_c) if total_c else 0, built, uploaded),
            "info", "命中 = 播放列表里带台标地址的频道数。没有台标不影响播放，只是频道列表里不显示图标。"))

        # 有节目表的频道数：按 channels.json 逐个频道去节目单里核对才算得准
        channels = _load_json(os.path.join(playlists_dir, "channels.json")).get("channels")
        matched, known = min(manifest.get("epg_channels") or 0, total_c), False
        if isinstance(channels, list) and os.path.isfile(out_epg):
            with_prog = epg_programme_channels(out_epg)
            matched, known = sum(1 for c in channels if isinstance(c, dict) and c.get("tvg_id") in with_prog), True
        rows.append(row(
            "节目单关联", "%d / %d 个频道有节目单（%d%%）· %s 条节目" % (
                matched, total_c, round(100.0 * matched / total_c) if total_c else 0,
                "{:,}".format(manifest.get("epg_programmes") or 0)),
            "ok" if matched else "fail",
            ("播放列表里的频道，至少有一条节目的数量。没有节目的频道在播放器里不显示预告。"
             if known else "节目单里的频道数（含播放列表以外的频道）与节目条数。")
            + ("" if matched else "一条节目都没有，播放器将看不到节目预告。")))
    else:
        rows.append(row("整理结果", "还没有整理结果", "fail", "请先点「立即整理」或「抓取并整理」。"))
    section("数据与整理结果", "抓取层产出的源数据，以及整理层据此生成的播放列表。", rows)

    # ------------------------------------------------------------ 节目单覆盖
    sched = ctx.get("schedule") or {}
    rows, chips = [], []
    if os.path.isfile(out_epg):
        counts = epg_day_counts(out_epg)
        rng, ndays = _range_text(counts)
        rows.append(row("覆盖范围", rng, "ok" if ndays else "fail",
                        "播放器实际拿到的节目单（%s）里有节目的日期。" % _rel(root, out_epg)))
        n_today = counts.get(today_key, 0)
        rows.append(row("今天的节目", "{:,} 条".format(n_today), "ok" if n_today else "fail",
                        "" if n_today else "今天一条节目都没有，播放器上的节目预告会是空的。"))

        want_f = max(1, int(sched.get("epg_days", 3) or 3))
        ahead = [today + timedelta(days=i) for i in range(1, want_f + 1)]
        lack = [d for d in ahead if not counts.get(d.strftime("%Y%m%d"), 0)]
        rows.append(row(
            "未来 %d 天" % want_f, "%d / %d 天有节目" % (want_f - len(lack), want_f), "ok" if not lack else "warn",
            "设置里保留未来 %d 天的节目单，对应播放器能预告的天数。%s" % (
                want_f, "" if not lack else "缺：" + "、".join(_md(d) for d in lack) + "。点「抓取并整理」补上。")))

        want_p = int(sched.get("epg_past_days", 7) or 0)
        if sched.get("epg_window", True) and want_p:
            ago = [today - timedelta(days=i) for i in range(1, want_p + 1)]
            lack = [d for d in ago if not counts.get(d.strftime("%Y%m%d"), 0)]
            rows.append(row(
                "回看 %d 天" % want_p, "%d / %d 天有节目" % (want_p - len(lack), want_p), "ok" if not lack else "warn",
                "开启滑动窗口后保留过去 %d 天的节目单，用于回看。缺的天数说明历史归档不全，不影响收看。" % want_p))

        for key in sorted(counts):
            cls = "today" if key == today_key else ("has" if counts[key] else "empty")
            chips.append({"label": key[4:6] + "-" + key[6:8], "count": counts[key], "class": cls})
    else:
        rows.append(row("输出节目单", "文件不存在", "fail", "请先点「立即整理」生成。"))
    section("节目单覆盖", "按日期检查节目单是否完整，这是「数据新不新」最直接的指标。", rows)
    if chips:
        sections[-1]["chips"] = chips

    # ------------------------------------------------------------ 任务与计划
    rows = []
    kind = "抓取并整理" if state.get("mode") == "full" else "仅整理"
    if state.get("running"):
        rows.append(row(
            "当前任务", "%s（%s）· 开始于 %s" % (kind, _trigger_text(state.get("trigger")), state.get("started_at") or "-"),
            "info", "任务结束后播放列表才会更新，期间不要重复点击。"))
    else:
        rows.append(row("当前任务", "空闲", "ok", "随时可以点「立即整理」或「抓取并整理」。"))

    if state.get("ok") is True:
        rows.append(row(
            "上次任务", "成功 · 完成于 %s" % state.get("finished_at"), "ok",
            "方式：%s · 类型：%s" % (_trigger_text(state.get("trigger")), kind)))
    elif state.get("ok") is False:
        rows.append(row("上次任务", "失败：" + (state.get("error") or "原因未知"), "fail",
                        "具体过程见控制台「运行日志」。"))
    else:
        rows.append(row("上次任务", "服务启动后还没有运行过任务", "info"))

    next_run = ctx.get("next_run")
    if sched.get("enabled"):
        plan = ("每天 %s" % sched.get("daily_time", "04:30")) if sched.get("mode") == "daily" \
            else ("每 %d 分钟一次" % int(sched.get("interval_minutes", 360) or 360))
        overdue = bool(next_run) and now - next_run > 120
        rows.append(row(
            "定时整理", "%s · 下次 %s" % (plan, _ts_text(next_run) if next_run else "调度器安排中"),
            "warn" if overdue else "ok",
            "定时任务会%s重新抓取频道与 EPG 再整理。%s" % (
                "" if sched.get("run_fetch", True) else "不",
                "已到定时时间但任务没有启动，请查看「运行日志」。" if overdue else "")))
    else:
        rows.append(row("定时整理", "未启用", "off", "需要更新时在控制台手动点「立即整理」或「抓取并整理」。"))
    section("任务与计划", "整理任务刚才跑得怎么样、下次什么时候跑。", rows)

    # ------------------------------------------------------------ 配置与连通性
    rows = []
    stb = _load_json(ctx.get("config_path") or "")
    proxy = ctx.get("proxy") or {}
    auth_ip = str(stb.get("AuthenticationIP") or "").strip()

    # 探测并行做：两个地址最坏情况各等 2 秒，不让页面干等
    targets = [(k, (proxy.get(k) or "").strip()) for k, _ in PROXY_KEYS]
    targets.append(("__auth__", auth_ip))
    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        results = list(pool.map(tcp_reachable, [u for _, u in targets]))
    probe = {k: r for (k, _), r in zip(targets, results)}

    any_proxy = False
    for key, label in PROXY_KEYS:
        url = (proxy.get(key) or "").strip()
        if not url:
            continue
        any_proxy = True
        text, st = _probe_text(probe[key])
        rows.append(row(
            label, "%s · %s" % (url, text), st,
            "播放器靠它把内网组播转成 HTTP。"
            + ("地址应是 http://内网IP:端口 的形式，请检查「整理设置」。" if probe[key] is None
               else "连不上时播放器里的频道一个都打不开，请确认代理程序在运行、地址端口没写错。")))
    if not any_proxy:
        rows.append(row("组播代理", "未配置", "warn", "播放列表里的地址将无法播放，请在「整理设置」里填写。"))

    if auth_ip:
        # 连不上只影响「更新数据」，已有的播放列表照样能看，所以不算异常
        rows.append(row(
            "认证服务器", "%s · %s" % (auth_ip, _probe_text(probe["__auth__"])[0]),
            "ok" if probe["__auth__"] is True else "warn",
            "「抓取并整理」要从这台内网服务器取频道和节目单。"
            + ("地址应是 http://内网IP:端口 的形式，请检查「机顶盒配置」。" if probe["__auth__"] is None
               else "连不上时已有的播放列表照样能看，但数据更新不了。")))
    else:
        rows.append(row("认证服务器", "未填写", "warn", "「抓取并整理」会失败，请先在「机顶盒配置」里填写。"))

    missing = [k for k in FETCH_REQUIRED
               if not str(stb.get(k) or "").strip()
               or str(stb.get(k)).strip().startswith("<")]
    rows.append(row(
        "机顶盒配置", "必填项齐全" if not missing else "缺：" + "、".join(missing),
        "ok" if not missing else "warn",
        "抓取时用来向认证服务器表明机顶盒身份的几项。只检查填没填，不显示内容。"))

    on = bool(ctx.get("protect_media"))
    rows.append(row(
        "订阅令牌保护", "已开启" if on else "未开启", "info",
        "播放列表 / 台标 / EPG 必须带令牌的地址才能访问，令牌可在控制台「安全设置」里重置。" if on
        else "内网里的设备都可以直接订阅。需要限制时在「安全设置」里开启。"))

    local_ver = (ctx.get("organize_cfg") or {}).get("groups_version")
    default_ver = ctx.get("groups_version_default")
    if local_ver == "manual":
        rows.append(row("分组规则", "自定义（manual）", "info", "分组规则是手动改过的，升级镜像时不会被覆盖。"))
    elif default_ver is None or local_ver == default_ver:
        rows.append(row("分组规则", "版本 %s（与当前版本一致）" % (local_ver or "-"), "ok"))
    else:
        rows.append(row(
            "分组规则", "本地 %s，应为 %s" % (local_ver or "无", default_ver), "warn",
            "下次读取配置时会自动升级分组规则：只替换分组这一项，代理地址、定时计划等设置原样保留。"))

    out_dir = ctx.get("output_dir") or ""
    writable = os.access(playlists_dir or out_dir, os.W_OK)
    free = shutil.disk_usage(out_dir).free if os.path.isdir(out_dir) else 0
    rows.append(row(
        "输出目录与磁盘", "%s · 剩余 %s · 输出占用 %s" % (
            "可写" if writable else "不可写", _size_text(free), _size_text(_dir_size(out_dir))),
        "ok" if writable and free >= (1 << 30) else ("fail" if not writable else "warn"),
        "输出目录是 %s。剩余空间不足 1 GiB 时节目单归档可能写不下。" % _rel(root, out_dir)))
    section("配置与连通性", "播放和抓取依赖的内网地址此刻连不连得上，各项设置是否完整。", rows)

    return _finish(sections, tally, now)


def _finish(sections: list, tally: Counter, now: float) -> dict:
    status = "fail" if tally["fail"] else ("warn" if tally["warn"] else "ok")
    return {
        "status": status,
        "counts": {k: tally[k] for k in ("fail", "warn", "ok", "info", "off")},
        "sections": sections,
        "time": datetime.fromtimestamp(now).strftime(TIME_FMT),
    }
