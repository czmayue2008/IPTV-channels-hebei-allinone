#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""整理层入口：把本项目抓取产物整理成多播放器兼容的播放列表与 EPG。

既可以被 app.py 直接 import 调用（手动整理 / 定时整理），也可以独立运行：

    python organize.py
    python organize.py --preset lean
    python organize.py --no-epg --exclude "4K,慢直播"

输入只读 output/hbtv.m3u 与 output/epg.xml；输出写到 output/playlists/，
两者互不覆盖。整理逻辑移植自 suzukua/iptv-cd-telecom 的节目单整理层，
数据源适配为河北电信。纯标准库，零外部网络请求。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from lib.epg import build_epg                       # noqa: E402
from lib.exporters import render_all, render_index  # noqa: E402
from lib.logo import LogoIndex                      # noqa: E402
from lib.normalize import build_channels            # noqa: E402
from lib.source import parse_epg, parse_m3u         # noqa: E402

CST = timezone(timedelta(hours=8))
ORGANIZE_CONFIG = os.path.join(ROOT, "config", "organize.json")


def resolve(path: str) -> str:
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(ROOT, path))


def now_str() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")


def load_config(path: str = ORGANIZE_CONFIG, overrides: dict | None = None) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    preset = (overrides or {}).pop("preset", None)
    if preset is None:
        preset = cfg.get("preset", "")
    cfg["preset"] = preset
    if preset:
        p = resolve(os.path.join("presets", preset + ".json"))
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k, v in data.items():
            if not k.startswith("_"):
                cfg[k] = v
    for k, v in (overrides or {}).items():
        cfg[k] = v
    return cfg


def run(cfg: dict | None = None, emit=None, with_epg: bool = True) -> dict:
    """执行一次整理。返回统计与全部输出文件名。emit 为逐行日志回调。"""
    emit = emit or (lambda _s: None)
    cfg = cfg if cfg is not None else load_config()

    src_m3u = resolve(cfg["source"]["playlist"])
    src_epg = resolve(cfg["source"]["epg"])
    out_dir = resolve(cfg["output_dir"])
    os.makedirs(out_dir, exist_ok=True)

    emit("源播放列表：%s" % os.path.relpath(src_m3u, ROOT).replace("\\", "/"))
    emit("源 EPG    ：%s" % os.path.relpath(src_epg, ROOT).replace("\\", "/"))
    raw = parse_m3u(src_m3u)
    emit("读取频道  ：%d 个" % len(raw))
    if not raw:
        raise RuntimeError("播放列表为空，请先执行「抓取并整理」生成 output/hbtv.m3u")

    source_channels, programmes = [], []
    if with_epg:
        source_channels, programmes = parse_epg(src_epg)
        emit("读取节目  ：%d 条 / %d 个频道" % (len(programmes), len(source_channels)))

    logo_dir = resolve(cfg.get("logo", {}).get("dir", "logo"))
    logos = LogoIndex(logo_dir, cfg.get("logo", {}).get("aliases", {}))
    channels = build_channels(raw, cfg, logos.lookup)
    hit = sum(1 for c in channels if c.logo)
    emit("整理频道  ：%d 个（台标库 %d 个，命中 %d 个）" % (len(channels), logos.count(), hit))
    if cfg.get("exclude_keywords") or cfg.get("include_keywords") or cfg.get("preset"):
        emit("过滤条件  ：preset=%s exclude=%s include=%s"
             % (cfg.get("preset") or "-", cfg.get("exclude_keywords"), cfg.get("include_keywords")))

    order = []
    for c in channels:
        if c.group not in order:
            order.append(c.group)
    emit("分组统计  ：" + "、".join("%s %d" % (g, sum(1 for c in channels if c.group == g)) for g in order))

    cfg["_has_epg"] = with_epg
    files = render_all(channels, cfg)

    # 每个 .m3u8 额外输出一份同名 .m3u，方便只认 .m3u 的播放器
    if cfg.get("write_m3u_alias", True):
        for fn in [x for x in list(files) if x.endswith(".m3u8")]:
            files[fn[:-1]] = files[fn]          # hbtv_aptv.m3u8 -> hbtv_aptv.m3u

    if with_epg:
        xml, gz = build_epg(channels, source_channels, programmes, cfg)
        files["epg.xml"] = xml
        root = ET.fromstring(xml)
        emit("EPG 重建  ：%d 个 channel / %d 条 programme"
             % (len(root.findall("channel")), len(root.findall("programme"))))

    # 重建订阅页，让它列出全部输出文件（含 .m3u 别名与 EPG）
    files["index.html"] = render_index(channels, cfg, files)

    written = []
    for name in sorted(files):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(files[name])
        written.append(name)
    if with_epg:
        with open(os.path.join(out_dir, "epg.xml.gz"), "wb") as f:
            f.write(gz)
        written.append("epg.xml.gz")

    playlists = [n for n in written if n.endswith((".m3u", ".m3u8", ".txt"))]
    manifest = {
        "generated_at": now_str(),
        "channel_count": len(channels),
        "group_counts": {g: sum(1 for c in channels if c.group == g) for g in order},
        "logo_hit": hit,
        "epg_channels": len(root.findall("channel")) if with_epg else 0,
        "epg_programmes": len(root.findall("programme")) if with_epg else 0,
        "playlists": sorted(playlists),
        "files": sorted(written),
    }
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")

    emit("输出目录  ：%s" % os.path.relpath(out_dir, ROOT).replace("\\", "/"))
    emit("播放列表  ：共 %d 个 → %s" % (len(playlists), ", ".join(sorted(playlists))))
    for name in sorted(written):
        emit("  %-28s %8.1f KiB" % (name, os.path.getsize(os.path.join(out_dir, name)) / 1024))
    emit("整理完成。")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description="河北电信 IPTV 节目单整理器（纯内网）")
    ap.add_argument("--config", default=ORGANIZE_CONFIG)
    ap.add_argument("--preset", default=None, help="预设名：lean / core")
    ap.add_argument("--exclude", default=None, help="排除关键字，逗号分隔")
    ap.add_argument("--include", default=None, help="只保留关键字，逗号分隔")
    ap.add_argument("--udpxy", default=None, help="覆盖 udpxy 内网地址")
    ap.add_argument("--no-epg", action="store_true", help="不生成 EPG")
    a = ap.parse_args()

    ov = {}
    if a.preset is not None:
        ov["preset"] = a.preset
    if a.exclude is not None:
        ov["exclude_keywords"] = [x.strip() for x in a.exclude.split(",") if x.strip()]
    if a.include is not None:
        ov["include_keywords"] = [x.strip() for x in a.include.split(",") if x.strip()]
    cfg = load_config(a.config, ov)
    if a.udpxy:
        cfg["proxy"]["udpxy"] = a.udpxy
    run(cfg, emit=print, with_epg=not a.no_epg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
