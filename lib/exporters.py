#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多播放器格式导出。

对应 iptv-cd-telecom 的三种 m3u8（标准 / APTV / udpxy）以及 Cloudflare
Functions 的动态参数能力，这里扩展为 7 种本地产物 + 一个动态 HTTP 服务。

河北与成都的两点差异：
- 时移源已被 rtp2httpd 包装为 http://.../rtsp/...，播放器无需原生 RTSP 支持；
- 主播放地址是组播代理 /rtp/239.x.x.x:port，可带 fcc= / fec= 快速换台参数。
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from .normalize import Channel

CST = timezone(timedelta(hours=8))

# 两套时移时间模板：KODI/ffmpegdirect 与 APTV/mytv-android
KODI_SEEK = "{utc:YmdHMS}-{utcend:YmdHMS}"
APTV_SEEK = "${(b)yyyyMMddHHmmss}-${(e)yyyyMMddHHmmss}"
PLAYSEEK_RE = re.compile(r"playseek=[^&]*")


def now_str() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")


def with_playseek(url: str, template: str) -> str:
    """把时移源里的 playseek 换成目标播放器的时间模板。"""
    if not url:
        return ""
    if PLAYSEEK_RE.search(url):
        return PLAYSEEK_RE.sub(lambda _m: "playseek=" + template, url)
    sep = "&" if "?" in url else "?"
    return url + sep + "playseek=" + template


def replace_origin(url: str, new_base: str) -> str:
    """只替换 URL 的 scheme://host:port，保留路径与查询串。"""
    if not new_base:
        return url
    p, q = urlsplit(url), urlsplit(new_base)
    return urlunsplit((q.scheme or p.scheme, q.netloc or p.netloc,
                       p.path, p.query, p.fragment))


def _set_param(url: str, key: str, value: str, drop: bool = False) -> str:
    """设置 / 覆盖 / 删除查询参数（只动查询串，保留路径与 fragment）。"""
    base, sep, query = url.partition("?")
    items = [x for x in (query.split("&") if sep else []) if x and not x.startswith(key + "=")]
    if value and not drop:
        items.append("%s=%s" % (key, value))
    return base + ("?" + "&".join(items) if items else "")


def main_url(ch: Channel, cfg: dict, style: str) -> str:
    """按目标格式生成主播放地址。"""
    r = ch.raw
    proxy = cfg.get("proxy", {})
    fcc = cfg.get("fcc") or r.fcc
    url = r.url

    if style == "udpxy":
        base = (proxy.get("udpxy") or "").rstrip("/")
        if base and r.multicast:
            url = "%s/udp/%s" % (base, r.multicast)
            url = _set_param(url, "fcc", fcc, drop=not fcc)
            if proxy.get("r2h_token"):
                url = _set_param(url, "r2h-token", proxy["r2h_token"])
        return url

    # 其余格式沿用 rtp2httpd 地址，可整体替换代理主机。
    # 未显式配置覆盖项时保持源 URL 原样（含 fcc / fec 参数顺序）。
    url = replace_origin(url, proxy.get("rtp2httpd", ""))
    if cfg.get("fcc"):
        url = _set_param(url, "fcc", cfg["fcc"])
    if not cfg.get("keep_fec", True):
        url = _set_param(url, "fec", "", drop=True)
    if proxy.get("r2h_token"):
        url = _set_param(url, "r2h-token", proxy["r2h_token"])
    return url


def catchup_url(ch: Channel, cfg: dict, seek: str) -> str:
    url = ch.raw.catchup_source
    proxy = cfg.get("proxy", {})
    if proxy.get("rtsp_proxy"):
        p = urlsplit(url)
        # http://r2h/rtsp/host/... -> <rtspProxy>/rtsp/host/...
        m = re.match(r"^/(?:rtsp|rtp)/(.*)$", p.path)
        if m:
            url = (proxy["rtsp_proxy"].rstrip("/") + "/rtsp/" + m.group(1)
                   + ("?" + p.query if p.query else ""))
    if proxy.get("r2h_token"):
        url = _set_param(url, "r2h-token", proxy["r2h_token"])
    return with_playseek(url, seek)


def logo_url(ch: Channel, cfg: dict) -> str:
    if not ch.logo:
        return ""
    base = cfg.get("logo", {}).get("base_url", "")
    if not base:
        return "logo/" + ch.logo
    return base.rstrip("/") + "/" + ch.logo


def header(cfg: dict) -> str:
    epg = cfg.get("epg_url", "")
    return ('#EXTM3U name="%s - %s" x-tvg-url="%s" url-tvg="%s"\n'
            % (cfg.get("list_name", "河北电信 IPTV"), now_str(), epg, epg))


def _esc(v: str) -> str:
    return (v or "").replace('"', "%22").replace("\n", " ").strip()


def extinf(ch: Channel, cfg: dict, *, style: str, seek: str = APTV_SEEK,
           kodi_prop: bool = False, catchup: bool = True) -> str:
    a = ['tvg-logo="%s"' % _esc(logo_url(ch, cfg)),
         'tvg-id="%s"' % _esc(ch.tvg_id),
         'tvg-name="%s"' % _esc(ch.tvg_name)]
    if catchup and ch.raw.catchup_source:
        src = catchup_url(ch, cfg, seek)
        a += ['catchup="default"',
              'catchup-days="%d"' % (ch.catchup_days or cfg.get("catchup_days_default", 1)),
              'catchup-source="%s"' % _esc(src)]
    a.append('group-title="%s"' % _esc(ch.group))
    line = "#EXTINF:-1 " + " ".join(a) + "," + ch.tvg_name + "\n"
    if kodi_prop:
        line = "#KODIPROP:inputstream=inputstream.ffmpegdirect\n" + line
    return line


def render_m3u(channels: list[Channel], cfg: dict, *, style: str,
               seek: str = APTV_SEEK, kodi_prop: bool = False,
               catchup: bool = True) -> str:
    out = [header(cfg)]
    for ch in channels:
        out.append(extinf(ch, cfg, style=style, seek=seek,
                          kodi_prop=kodi_prop, catchup=catchup))
        out.append(main_url(ch, cfg, style) + "\n")
    return "".join(out)


def render_diyp(channels: list[Channel], cfg: dict) -> str:
    """DIYP / 影视仓：名称,时移源#组播代理；分组用 xxx,#genre#。"""
    out, last_group = [], None
    for ch in channels:
        if ch.group != last_group:
            out.append("%s,#genre#\n" % ch.group)
            last_group = ch.group
        live = main_url(ch, cfg, "rtp2httpd")
        if ch.raw.catchup_source:
            # DIYP 约定：时移源在前，# 后为直播源
            src = with_playseek(ch.raw.catchup_source, APTV_SEEK)
            out.append("%s,%s#%s\n" % (ch.tvg_name, src, live))
        else:
            out.append("%s,%s\n" % (ch.tvg_name, live))
    return "".join(out)


def render_simple(channels: list[Channel], cfg: dict) -> str:
    """最简 txt：名称,播放地址。兼容老播放器与自定义壳。"""
    return "".join("%s,%s\n" % (ch.tvg_name, main_url(ch, cfg, "rtp2httpd"))
                   for ch in channels)


def render_channels_json(channels: list[Channel], cfg: dict) -> str:
    data = {
        "generated_at": now_str(),
        "source": "本项目 output/hbtv.m3u（只读） + 本地整理层",
        "count": len(channels),
        "groups": list(dict.fromkeys(c.group for c in channels)),
        "channels": [{
            "tvg_id": c.tvg_id,
            "tvg_name": c.tvg_name,
            "raw_name": c.raw.name,
            "group": c.group,
            "logo": logo_url(c, cfg),
            "is_4k": c.is_4k,
            "url": main_url(c, cfg, "rtp2httpd"),
            "udpxy_url": main_url(c, cfg, "udpxy"),
            "multicast": c.raw.multicast,
            "fcc": c.raw.fcc,
            "fec": c.raw.fec,
            "catchup_source": with_playseek(c.raw.catchup_source, APTV_SEEK),
            "catchup_days": c.catchup_days,
        } for c in channels],
    }
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


FORMATS = {
    "kodi": dict(file="hbtv_kodi.m3u8", label="KODI / IPTV Simple / ffmpegdirect",
                 seek=KODI_SEEK, kodi_prop=True, catchup=True,
                 note="带 #KODIPROP 与 KODI 时移模板，适合 KODI / CoreELEC"),
    "aptv": dict(file="hbtv_aptv.m3u8", label="APTV / mytv-android",
                 seek=APTV_SEEK, kodi_prop=False, catchup=True,
                 note="APTV 时区兼容时移模板，适合 APTV、mytv-android、完美解码"),
    "tvbox": dict(file="hbtv_tvbox.m3u8", label="TVBox / Fileball / 通用",
                  seek=APTV_SEEK, kodi_prop=False, catchup=False,
                  note="不含 KODIPROP 与 catchup 的纯净 m3u，兼容性最广"),
    "udpxy": dict(file="hbtv_udpxy.m3u8", label="udpxy / msd_lite",
                  seek=APTV_SEEK, kodi_prop=False, catchup=True, style="udpxy",
                  note="主地址改为 /udp/组播，适合 udpxy / msd_lite 组播转单播"),
    "rtp2httpd": dict(file="hbtv_rtp2httpd.m3u8", label="rtp2httpd 增强版",
                      seek=APTV_SEEK, kodi_prop=False, catchup=True,
                      note="沿用现有项目播放地址，补齐台标 / tvg-id / tvg-name"),
}


def render_all(channels: list[Channel], cfg: dict) -> dict[str, str]:
    """返回 {文件名: 内容}。"""
    files: dict[str, str] = {}
    for key, spec in FORMATS.items():
        if key not in cfg.get("formats", list(FORMATS)):
            continue
        files[spec["file"]] = render_m3u(
            channels, cfg, style=spec.get("style", "rtp2httpd"),
            seek=spec["seek"], kodi_prop=spec["kodi_prop"],
            catchup=spec["catchup"])
    if "diyp" in cfg.get("formats", []):
        files["hbtv_diyp.txt"] = render_diyp(channels, cfg)
    if "simple" in cfg.get("formats", []):
        files["hbtv_simple.txt"] = render_simple(channels, cfg)
    files["channels.json"] = render_channels_json(channels, cfg)
    files["index.html"] = render_index(channels, cfg, files)
    return files


def render_index(channels: list[Channel], cfg: dict, files: dict[str, str]) -> str:
    host = cfg.get("epg_url", "/playlists/epg.xml")
    labels = {s["file"]: (s["label"], s["note"]) for s in FORMATS.values()}
    labels.update({
        "hbtv_diyp.txt": ("DIYP / 影视仓", "名称,时移源#组播代理，带 #genre# 分组"),
        "hbtv_simple.txt": ("极简 txt", "名称,播放地址，兼容老播放器"),
        "epg.xml": ("XMLTV 电子节目单", "已按规范化 tvg-id 重建，4K 频道共用标清 EPG"),
        "epg.xml.gz": ("XMLTV 压缩版", "部分播放器只接受 .gz 格式"),
        "channels.json": ("结构化频道数据", "便于二次开发或适配其它播放器"),
        "manifest.json": ("输出清单", "本次整理生成的文件名与统计"),
    })
    # 遍历实际输出，自动把 .m3u 别名也列出来
    rows = []
    for fn in sorted(files):
        if fn == "index.html":
            continue
        if fn in labels:
            lab, note = labels[fn]
        elif fn.endswith(".m3u") and (fn + "8") in labels:
            lab, note = labels[fn + "8"]
            lab += "（.m3u 别名）"
        else:
            lab, note = "附加文件", ""
        rows.append((fn, lab, note))
    if cfg.get("_has_epg", True):
        for fn in ("epg.xml", "epg.xml.gz"):
            if fn not in files:
                rows.append((fn, *labels[fn]))

    trs = "\n".join(
        '<tr><td><code>%s</code></td><td>%s</td><td class="muted">%s</td>'
        '<td><button onclick="cp(\'%s\')">复制地址</button></td></tr>'
        % (html.escape(fn), html.escape(label), html.escape(note), html.escape(fn))
        for fn, label, note in rows)

    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s - 订阅中心</title>
<style>
body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:#f6f7f9;color:#1b1f24;margin:0;padding:28px}
.wrap{max-width:1000px;margin:auto}
h1{font-size:24px;margin:0 0 6px}h2{font-size:17px;margin:28px 0 10px}
.muted{color:#6b7280;font-size:13px}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:20px;box-shadow:0 1px 3px rgba(0,0,0,.04)}
table{width:100%%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:10px 8px;border-bottom:1px solid #eef0f3;vertical-align:top}
th{color:#6b7280;font-weight:600;font-size:13px}
code{background:#f3f4f6;padding:2px 6px;border-radius:4px;font-size:13px}
button{border:1px solid #d1d5db;background:#fff;border-radius:6px;padding:5px 10px;cursor:pointer;font-size:12px}
button:hover{background:#f3f4f6}
.stat{display:flex;gap:26px;flex-wrap:wrap;margin:12px 0 0}
.stat b{font-size:22px;display:block;color:#2563eb}
pre{background:#0f172a;color:#e2e8f0;padding:14px;border-radius:8px;overflow:auto;font-size:12px}
</style></head><body><div class="wrap">
<h1>%s · 订阅中心</h1>
<div class="muted">由本项目整理层生成于 %s ｜ 数据源：本项目 output/ 抓取产物（只读）｜ 台标、EPG 与播放列表均由本服务在内网提供</div>
<div class="card" style="margin-top:18px">
<div class="stat"><div><b>%d</b><span class="muted">频道</span></div>
<div><b>%d</b><span class="muted">分组</span></div>
<div><b>%s</b><span class="muted">EPG 地址</span></div></div>
</div>
<h2>可用订阅文件</h2>
<div class="card"><table><thead><tr><th>文件</th><th>适用播放器</th><th>说明</th><th></th></tr></thead>
<tbody>%s</tbody></table></div>
<h2>动态参数（所有 .m3u8 都支持）</h2>
<div class="card"><pre>?aptv=1                        # KODI 时移模板 -&gt; APTV 模板
?proxy=http://192.168.1.10:8888 # 替换 rtp2httpd / udpxy 代理地址
?fcc=192.168.40.10:8027         # 覆盖 FCC 快速换台服务器
?r2hToken=xxxx                  # rtp2httpd 认证令牌
?rtspProxy=http://192.168.1.10:4022
?exclude=4K,慢直播,购物          # 排除频道（匹配 tvg-name）
?include=CCTV,卫视              # 只保留频道
?logo=http://192.168.1.10:8080/logo/  ?epg=http://.../epg.xml</pre>
<p class="muted">示例：%s</p></div>
<p class="muted" style="margin-top:22px">台标文件随本项目存放在内网，不访问任何外部地址。播放地址与 EPG 仅在内网 IPTV 专网可用。</p>
</div>
<script>
function cp(f){const u=new URL(f,location.href).href;navigator.clipboard&&navigator.clipboard.writeText(u).then(()=>alert('已复制：\\n'+u),()=>prompt('复制地址：',u))||prompt('复制地址：',u)}
</script></body></html>
""" % (html.escape(cfg.get("list_name", "河北电信 IPTV")),
       html.escape(cfg.get("list_name", "河北电信 IPTV")), now_str(),
       len(channels), len(set(c.group for c in channels)),
       html.escape(host), trs,
       html.escape("/playlists/hbtv_aptv.m3u8?aptv=1&exclude=4K,慢直播"))
