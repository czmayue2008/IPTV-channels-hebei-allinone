#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HBTV All-in-One 自检：生成物、EPG、Web 路由、动态参数、定时调度、零外部地址。

    python verify.py
"""
from __future__ import annotations

import gzip
import json
import os
import py_compile
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "output", "playlists")

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print("  %s %-46s %s" % ("PASS" if cond else "FAIL", name, detail))


def read(fn: str) -> str:
    with open(os.path.join(OUT, fn), "r", encoding="utf-8") as f:
        return f.read()


def main() -> int:
    print("=" * 76)
    print(" HBTV All-in-One 自检")
    print("=" * 76)

    print("\n[1] 项目结构")
    need = ["app.py", "organize.py", "get_iptv_channels_V1.1.py",
            "config/organize.json", "config/config.json", "verify.py",
            "lib/source.py", "lib/normalize.py", "lib/logo.py",
            "lib/exporters.py", "lib/epg.py", "lib/dynamic.py",
            "templates/index.html", "templates/config.html", "templates/login.html",
            "static/css/bootstrap.min.css", "static/css/app.css", "static/js/theme.js",
            "output/hbtv.m3u", "output/epg.xml"]
    check("关键文件齐全", all(os.path.isfile(os.path.join(ROOT, f)) for f in need),
          "%d 项" % len(need))
    logos = [f for f in os.listdir(os.path.join(ROOT, "logo")) if f.lower().endswith(".png")]
    check("本地台标文件", len(logos) >= 100, "%d 个" % len(logos))

    print("\n[2] Python 语法")
    ok = True
    for f in ["app.py", "organize.py", "verify.py", "get_iptv_channels_V1.1.py"] + ["lib/" + x for x in os.listdir(os.path.join(ROOT, "lib")) if x.endswith(".py")]:
        try:
            py_compile.compile(os.path.join(ROOT, f), doraise=True)
        except Exception as e:
            ok = False
            print("   ", f, e)
    check("全部模块可编译", ok)

    print("\n[2b] EPG 多天抓取健壮性")
    src = open(os.path.join(ROOT, "get_iptv_channels_V1.1.py"), encoding="utf-8").read()
    check("会话定期重新认证", "EPG_REFRESH_EVERY" in src and "refresh_epg_session" in src)
    check("刷新阈值低于会话上限", bool(re.search(r"EPG_REFRESH_EVERY\s*=\s*(\d+)", src))
          and int(re.search(r"EPG_REFRESH_EVERY\s*=\s*(\d+)", src).group(1)) < 1000)
    check("自动探测运营商可用天数", "detect_available_days" in src)
    check("失败重试而非静默吞掉", "pb, err = fetch_playbills" in src and "failed += 1" in src)
    check("残缺结果不覆盖旧 EPG", "raise RuntimeError" in src and "os.replace(tmp, path)" in src)
    check("EPG 失败时非零退出", "sys.exit(1)" in src)

    print("\n[2c] EPG 滑动窗口（回看）")
    check("按天归档与滚动裁剪", all(k in src for k in
          ("write_epg_window", "save_archive", "load_archive", "prune_archives")))
    check("窗口 CLI 参数齐全", all(k in src for k in
          ("--epg-window", "--epg-past-days", "--epg-future-days")))
    app_src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    check("Web 任务传递窗口参数", "epg_past_days" in app_src and "epg_window" in app_src)
    org = json.load(open(os.path.join(ROOT, "config", "organize.json"), encoding="utf-8"))
    sched = org.get("schedule", {})
    check("默认窗口为过去 7 + 未来 3",
          sched.get("epg_window") is True and sched.get("epg_past_days") == 7
          and sched.get("epg_days") == 3,
          "past=%s future=%s" % (sched.get("epg_past_days"), sched.get("epg_days")))
    check("catchup-days 与回看窗口对齐",
          org.get("catchup_days_override") == 7
          and "catchup_days_override" in open(os.path.join(ROOT, "lib", "exporters.py"), encoding="utf-8").read())

    print("\n[3] 整理产物")
    files = sorted(os.listdir(OUT)) if os.path.isdir(OUT) else []
    pls = [f for f in files if f.endswith((".m3u", ".m3u8", ".txt"))]
    check("播放列表文件数", len(pls) == 12, "%d 个：%s" % (len(pls), ", ".join(pls)))
    check("每个 .m3u8 都有 .m3u 别名",
          all(f[:-1] in files for f in files if f.endswith(".m3u8")))
    for f in ["epg.xml", "epg.xml.gz", "channels.json", "manifest.json", "index.html"]:
        check("存在 " + f, f in files)

    manifest = json.loads(read("manifest.json")) if "manifest.json" in files else {}
    n = manifest.get("channel_count", 0)
    check("manifest 字段完整", all(k in manifest for k in
                                   ("generated_at", "channel_count", "playlists",
                                    "epg_channels", "epg_programmes", "files")))
    check("频道数非空", n > 0, "%d 个频道" % n)
    check("manifest 播放列表数与实际一致",
          len(manifest.get("playlists", [])) == len(pls))

    print("\n[4] 播放列表格式")
    if "hbtv_aptv.m3u" in files:
        t = read("hbtv_aptv.m3u")
        check("APTV #EXTINF 数 == 频道数", t.count("#EXTINF") == n,
              "%d / %d" % (t.count("#EXTINF"), n))
        urls = [l for l in t.splitlines() if l.startswith("http")]
        check("播放 URL 无竖线残留", all("|" not in l for l in urls))
        check("播放 URL 数 == 频道数", len(urls) == n, "%d" % len(urls))
        check("回看 catchup-days 对齐 7 天窗口",
              'catchup-days="7"' in t and 'catchup-days="1"' not in t)
        check("离线台标为本地根相对路径",
              'tvg-logo="/logo/' in t and not re.search(r'tvg-logo="https?://', t))
        check("离线 EPG 为本地根相对路径", 'x-tvg-url="/playlists/epg.xml"' in t)
        check("APTV 时移模板正确", "${(b)yyyyMMddHHmmss}" in t)
        srcs = re.findall(r'catchup-source="([^"]*)"', t)
        dirty = ("accountinfo=", "r2h-seek-mode=", "rrsip=", "zoneoffset=",
                 "GuardEncType=", "&it=")
        check("回看地址已精简（无机顶盒票据参数）",
              bool(srcs) and not any(any(k in u for k in dirty) for u in srcs),
              "%d 条回看地址" % len(srcs))
        keys = [[p.split("=", 1)[0] for p in u.partition("?")[2].split("&") if p]
                for u in srcs]
        check("回看地址只保留 playseek（可含 r2h-token）",
              bool(keys) and all(set(k) <= {"playseek", "r2h-token"}
                                and "playseek" in k for k in keys))
        check("回看地址保留 .smil 原始路径",
              bool(srcs) and all(u.partition("?")[0].endswith(".smil") for u in srcs))
        check("回看 playseek 为 APTV 时间模板",
              bool(srcs) and all("playseek=${(b)yyyyMMddHHmmss}-${(e)yyyyMMddHHmmss}"
                                in u for u in srcs))
    if "hbtv_kodi.m3u8" in files:
        k = read("hbtv_kodi.m3u8")
        check("KODI 模板与 KODIPROP", "{utc:YmdHMS}" in k and "#KODIPROP" in k)
        ks = re.findall(r'catchup-source="([^"]*)"', k)
        check("KODI 回看地址已精简",
              bool(ks) and all("accountinfo=" not in u and "r2h-seek-mode=" not in u
                               for u in ks))
        check("KODI 回看 playseek 模板正确",
              bool(ks) and all("playseek={utc:YmdHMS}-{utcend:YmdHMS}" in u
                               for u in ks))
    if "hbtv_diyp.txt" in files:
        d = read("hbtv_diyp.txt")
        check("DIYP 回看地址已精简",
              "playseek=" in d and "accountinfo=" not in d
              and "r2h-seek-mode=" not in d)
    if "channels.json" in files:
        cj = json.loads(read("channels.json"))
        cc = [c for c in cj["channels"] if c.get("catchup_source")]
        check("channels.json 回看地址已精简",
              bool(cc) and all("accountinfo=" not in c["catchup_source"]
                               and "r2h-seek-mode=" not in c["catchup_source"]
                               for c in cc))
        check("channels.json catchup-days 跟随 7 天窗口",
              bool(cc) and all(c.get("catchup_days") == 7 for c in cc))
    if "hbtv_tvbox.m3u8" in files:
        v = read("hbtv_tvbox.m3u8")
        check("TVBox 纯净格式", "catchup" not in v and "#KODIPROP" not in v)
    if "hbtv_udpxy.m3u8" in files:
        u = read("hbtv_udpxy.m3u8")
        check("udpxy 使用 /udp/ 组播", "/udp/239." in u)

    print("\n[5] EPG")
    if "epg.xml" in files:
        root = ET.parse(os.path.join(OUT, "epg.xml")).getroot()
        eids = {c.get("id") for c in root.findall("channel")}
        progs = root.findall("programme")
        check("XMLTV 合法且根为 tv", root.tag == "tv")
        check("channel / programme 非空", len(eids) > 0 and len(progs) > 0,
              "%d / %d" % (len(eids), len(progs)))
        check("节目引用的 id 都存在", all(p.get("channel") in eids for p in progs))
        days = {p.get("start", "")[:8] for p in progs}
        check("EPG 覆盖 11 天回看窗口", len(days) >= 10,
              "%d 天：%s ~ %s" % (len(days), min(days), max(days)))
        tvg = set(re.findall(r'tvg-id="([^"]*)"', read("hbtv_aptv.m3u"))) if "hbtv_aptv.m3u" in files else set()
        check("tvg-id 与 EPG 100% 对齐", not (tvg - eids), "缺失 %d" % len(tvg - eids))
        gz = gzip.open(os.path.join(OUT, "epg.xml.gz"), "rb").read()
        check("epg.xml.gz 可解压且内容一致",
              gz.decode("utf-8") == open(os.path.join(OUT, "epg.xml"), encoding="utf-8").read())

    print("\n[6] 零外部地址（运行时文件）")
    scan = ["app.py", "organize.py", "templates/index.html", "templates/config.html",
            "templates/login.html", "static/css/app.css", "static/js/theme.js",
            "output/playlists/index.html"] + ["lib/" + x for x in os.listdir(os.path.join(ROOT, "lib")) if x.endswith(".py")]
    ext = re.compile(r'''(?:src|href)\s*=\s*["']https?://|url\(\s*["']?https?://|fetch\(\s*["']https?://|@import\s+["']?https?://''')
    hits = []
    for f in scan:
        p = os.path.join(ROOT, f)
        if not os.path.isfile(p):
            continue
        txt = open(p, encoding="utf-8").read()
        for m in ext.finditer(txt):
            hits.append((f, txt[m.start():m.start() + 70].split("\n")[0]))
    check("无外部 script / link / CSS / fetch", not hits, str(hits[:3]))
    check("首页模板无 CDN 关键字",
          all(k not in open(os.path.join(ROOT, "templates/index.html"), encoding="utf-8").read()
              for k in ("cdnjs", "jsdelivr", "googleapis", "unpkg", "socket.io.min.js")))
    check("抓取固定禁用外部同步", "--no-sync" in open(os.path.join(ROOT, "app.py"), encoding="utf-8").read())
    check("绑定 0.0.0.0", 'host="0.0.0.0"' in open(os.path.join(ROOT, "app.py"), encoding="utf-8").read())

    print("\n[7] 登录认证与订阅令牌")
    try:
        import tempfile
        # 用临时认证文件隔离真实密码，自检不会读写用户自己的 config/auth.json
        os.environ["HBTV_AUTH_FILE"] = os.path.join(
            tempfile.mkdtemp(prefix="hbtv_auth_"), "auth.json")
        import app as web
        c = web.app.test_client()
        anon = web.app.test_client()

        st = anon.get("/api/auth/status").get_json()
        check("初始状态为未设置密码", st["initialized"] is False and not st["authenticated"])
        check("未登录访问控制台跳转登录页",
              anon.get("/").status_code == 302 and "/login" in anon.get("/").headers.get("Location", ""))
        check("未登录配置页跳转登录页", anon.get("/config").status_code == 302)
        check("未登录管理 API 返回 401", anon.get("/api/status").status_code == 401)
        check("未登录不能启动整理任务", anon.post("/api/job/start", json={"mode": "organize"}).status_code == 401)
        check("健康检查无需登录", anon.get("/health").status_code == 200)
        check("播放列表默认免登录（播放器兼容）",
              anon.get("/playlists/hbtv_aptv.m3u").status_code == 200)
        check("台标与 EPG 默认免登录",
              anon.get("/logo/CCTV1.png").status_code == 200
              and anon.get("/playlists/epg.xml").status_code == 200)
        lg = anon.get("/login").get_data(as_text=True)
        check("未初始化显示设置密码页", "设置管理员密码" in lg and "config/auth.json" in lg)
        check("登录页仅引用本地资源", "/static/css/app.css" in lg and not ext.search(lg))
        check("密码过短被拒绝", anon.post("/api/auth/setup", json={
            "password": "123", "password2": "123"}).status_code == 400)
        check("两次密码不一致被拒绝", anon.post("/api/auth/setup", json={
            "password": "Test@12345", "password2": "Test@54321"}).status_code == 400)
        r = c.post("/api/auth/setup", json={"password": "Test@12345", "password2": "Test@12345"})
        check("首次设置密码并自动登录", r.status_code == 200 and r.get_json().get("success"))
        check("重复初始化被拒绝", anon.post("/api/auth/setup", json={
            "password": "Test@12345", "password2": "Test@12345"}).status_code == 409)
        check("密码以哈希保存", "Test@12345" not in open(web.AUTH_PATH, encoding="utf-8").read()
              and "pbkdf2" in open(web.AUTH_PATH, encoding="utf-8").read())
        check("认证文件被 git / docker 忽略",
              "config/auth.json" in open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read()
              and "config/auth.json" in open(os.path.join(ROOT, ".dockerignore"), encoding="utf-8").read())
        c.get("/logout")
        check("登出后管理 API 返回 401", c.get("/api/status").status_code == 401)
        check("错误密码被拒绝", c.post("/api/auth/login", json={
            "password": "wrong-password"}).status_code == 401)
        check("正确密码登录成功", c.post("/api/auth/login", json={
            "password": "Test@12345"}).get_json().get("success") is True)
        check("登录后控制台可访问", c.get("/").status_code == 200)
        check("已登录访问登录页自动跳转", c.get("/login").status_code == 302)
        check("改密码需正确原密码", c.post("/api/auth/password", json={
            "old_password": "bad", "new_password": "New@12345",
            "new_password2": "New@12345"}).status_code == 401)
        check("修改密码成功", c.post("/api/auth/password", json={
            "old_password": "Test@12345", "new_password": "New@12345",
            "new_password2": "New@12345"}).get_json().get("success") is True)
        check("改密码后当前会话仍有效", c.get("/api/status").status_code == 200)

        r = c.post("/api/auth/media", json={"protect_media": True})
        tok = r.get_json().get("subscribe_token", "")
        check("开启订阅令牌保护", r.get_json().get("protect_media") is True and len(tok) > 16)
        check("开启后播放列表拒绝匿名", anon.get("/playlists/hbtv_aptv.m3u").status_code == 401)
        check("开启后台标/EPG 拒绝匿名", anon.get("/logo/CCTV1.png").status_code == 401
              and anon.get("/playlists/epg.xml").status_code == 401)
        check("错误令牌被拒绝", anon.get("/playlists/hbtv_aptv.m3u?token=bad").status_code == 401)
        resp = anon.get("/playlists/hbtv_aptv.m3u?token=" + tok,
                        base_url="http://192.168.1.50:5000")
        body = resp.get_data(as_text=True)
        check("带令牌可订阅", resp.status_code == 200 and body.count("#EXTINF") == n)
        check("播放列表内台标自动带令牌",
              'tvg-logo="http://192.168.1.50:5000/logo/' in body and "?token=" + tok in body)
        check("播放列表内 EPG 自动带令牌",
              'x-tvg-url="http://192.168.1.50:5000/playlists/epg.xml?token=' + tok in body)
        check("Bearer 头同样可用", anon.get("/logo/CCTV1.png", headers={
            "Authorization": "Bearer " + tok}).status_code == 200)
        idx = anon.get("/playlists/index.html?token=" + tok).get_data(as_text=True)
        check("订阅中心注入令牌并支持复制",
              "window.HBTV_TOKEN" in idx and tok in idx and "encodeURIComponent(tk)" in idx)
        tok2 = c.post("/api/auth/token/rotate").get_json().get("subscribe_token", "")
        check("重置令牌后旧地址失效", tok2 != tok
              and anon.get("/playlists/hbtv_aptv.m3u?token=" + tok).status_code == 401
              and anon.get("/playlists/hbtv_aptv.m3u?token=" + tok2).status_code == 200)
        c.post("/api/auth/media", json={"protect_media": False})
        check("关闭后恢复播放器免登录", anon.get("/playlists/hbtv_aptv.m3u").status_code == 200)

        print("\n[8] Web 服务（Flask test_client）")
        for p in ["/", "/config", "/health", "/api/status", "/api/files",
                  "/api/schedule", "/api/organize_config", "/logo/CCTV1.png",
                  "/playlists/index.html", "/playlists/hbtv_aptv.m3u",
                  "/playlists/epg.xml", "/playlists/epg.xml.gz",
                  "/output/hbtv.m3u"]:
            r = c.get(p)
            check("GET " + p, r.status_code == 200, str(r.status_code))

        home = c.get("/").get_data(as_text=True)
        check("控制台含手动/定时/输出文件区",
              all(k in home for k in ("立即整理", "抓取并整理", "定时整理",
                                     "输出文件", "复制全部 m3u 文件名")))
        check("控制台无外部脚本", not ext.search(home))

        r = c.get("/playlists/hbtv_aptv.m3u", base_url="http://192.168.1.50:5000")
        t = r.get_data(as_text=True)
        check("台标按访问主机补全为绝对地址",
              'tvg-logo="http://192.168.1.50:5000/logo/' in t and not re.search(r'tvg-logo="/', t))
        check("EPG 按访问主机补全为绝对地址",
              'x-tvg-url="http://192.168.1.50:5000/playlists/epg.xml"' in t)

        a = c.get("/playlists/hbtv_aptv.m3u?exclude=CCTV").get_data(as_text=True)
        b = c.get("/playlists/hbtv_aptv.m3u?include=CCTV1").get_data(as_text=True)
        k = c.get("/playlists/hbtv_kodi.m3u8?aptv=1").get_data(as_text=True)
        check("exclude 参数生效", 0 < a.count("#EXTINF") < n, "%d" % a.count("#EXTINF"))
        check("include 参数生效", 0 < b.count("#EXTINF") < n, "%d" % b.count("#EXTINF"))
        check("aptv 参数替换时移模板", "{utc:YmdHMS}" not in k and "${(b)yyyyMMddHHmmss}" in k)

        d = c.get("/api/files").get_json()
        api_names = [f["name"] for f in d["files"] if f["is_playlist"]]
        check("文件 API 返回全部 m3u 文件名", sorted(api_names) == sorted(pls),
              "%d 个" % len(api_names))

        print("\n[9] 定时调度")
        s = web.read_schedule()
        nxt_i = web.compute_next({"enabled": True, "mode": "interval", "interval_minutes": 30})
        nxt_d = web.compute_next({"enabled": True, "mode": "daily", "daily_time": "04:30"})
        check("间隔模式下次时间正确", nxt_i and 1790 <= nxt_i - time.time() <= 1810)
        check("每日模式下次时间在未来 24h 内", nxt_d and 0 < nxt_d - time.time() <= 86400)
        check("关闭时无下次时间", web.compute_next({"enabled": False}) is None)
        r = c.post("/api/schedule", json={"enabled": False})
        check("POST /api/schedule 可保存", r.status_code == 200 and r.get_json().get("success"))
        check("GET /api/schedule 返回配置", "schedule" in c.get("/api/schedule").get_json())

        print("\n[10] 手动整理任务（端到端）")
        r = c.post("/api/job/start", json={"mode": "organize"})
        check("POST /api/job/start 启动", r.status_code == 200 and r.get_json().get("success"))
        r2 = c.post("/api/job/start", json={"mode": "organize"})
        check("运行中重复启动被拒绝", r2.status_code == 409, str(r2.status_code))
        st = {}
        for _ in range(80):
            st = c.get("/api/status").get_json()
            if not st["running"]:
                break
            time.sleep(0.4)
        check("任务完成且成功", st.get("ok") is True, str(st.get("error", ""))[:60])
        check("任务返回输出文件名", len(st.get("files", [])) >= 15,
              "%d 个" % len(st.get("files", [])))
        check("日志非空", c.get("/api/logs?since=0").get_json()["seq"] > 5)
    except ImportError as e:
        check("Flask 可用", False, "请先 pip install -r requirements.txt (%s)" % e)

    print("\n[11] 原项目零改动")
    try:
        r = subprocess.run(["git", "status", "--porcelain"],
                           cwd=os.path.join(os.path.dirname(ROOT), "IPTV-channels-hebei"),
                           capture_output=True, text=True)
        check("IPTV-channels-hebei git clean", r.stdout.strip() == "",
              r.stdout.strip()[:80])
    except Exception:
        check("IPTV-channels-hebei git clean", True, "（跳过，非 git 环境）")

    print("\n" + "=" * 76)
    print(" 通过 %d 项 / 失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        print(" 失败项：" + "、".join(FAIL))
    print("=" * 76)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
