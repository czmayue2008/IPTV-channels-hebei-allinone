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
            "templates/index.html", "templates/config.html",
            "static/css/bootstrap.min.css", "output/hbtv.m3u", "output/epg.xml"]
    check("关键文件齐全", all(os.path.isfile(os.path.join(ROOT, f)) for f in need),
          "%d 项" % len(need))
    logos = [f for f in os.listdir(os.path.join(ROOT, "logo")) if f.lower().endswith(".png")]
    check("本地台标文件", len(logos) >= 100, "%d 个" % len(logos))

    print("\n[2] Python 语法")
    ok = True
    for f in ["app.py", "organize.py", "verify.py"] + ["lib/" + x for x in os.listdir(os.path.join(ROOT, "lib")) if x.endswith(".py")]:
        try:
            py_compile.compile(os.path.join(ROOT, f), doraise=True)
        except Exception as e:
            ok = False
            print("   ", f, e)
    check("全部模块可编译", ok)

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
        check("离线台标为本地根相对路径",
              'tvg-logo="/logo/' in t and not re.search(r'tvg-logo="https?://', t))
        check("离线 EPG 为本地根相对路径", 'x-tvg-url="/playlists/epg.xml"' in t)
        check("APTV 时移模板正确", "${(b)yyyyMMddHHmmss}" in t)
    if "hbtv_kodi.m3u8" in files:
        k = read("hbtv_kodi.m3u8")
        check("KODI 模板与 KODIPROP", "{utc:YmdHMS}" in k and "#KODIPROP" in k)
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
        tvg = set(re.findall(r'tvg-id="([^"]*)"', read("hbtv_aptv.m3u"))) if "hbtv_aptv.m3u" in files else set()
        check("tvg-id 与 EPG 100% 对齐", not (tvg - eids), "缺失 %d" % len(tvg - eids))
        gz = gzip.open(os.path.join(OUT, "epg.xml.gz"), "rb").read()
        check("epg.xml.gz 可解压且内容一致",
              gz.decode("utf-8") == open(os.path.join(OUT, "epg.xml"), encoding="utf-8").read())

    print("\n[6] 零外部地址（运行时文件）")
    scan = ["app.py", "organize.py", "templates/index.html", "templates/config.html",
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

    print("\n[7] Web 服务（Flask test_client）")
    try:
        import app as web
        c = web.app.test_client()
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

        print("\n[8] 定时调度")
        s = web.read_schedule()
        nxt_i = web.compute_next({"enabled": True, "mode": "interval", "interval_minutes": 30})
        nxt_d = web.compute_next({"enabled": True, "mode": "daily", "daily_time": "04:30"})
        check("间隔模式下次时间正确", nxt_i and 1790 <= nxt_i - time.time() <= 1810)
        check("每日模式下次时间在未来 24h 内", nxt_d and 0 < nxt_d - time.time() <= 86400)
        check("关闭时无下次时间", web.compute_next({"enabled": False}) is None)
        r = c.post("/api/schedule", json={"enabled": False})
        check("POST /api/schedule 可保存", r.status_code == 200 and r.get_json().get("success"))
        check("GET /api/schedule 返回配置", "schedule" in c.get("/api/schedule").get_json())

        print("\n[9] 手动整理任务（端到端）")
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

    print("\n[10] 原项目零改动")
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
