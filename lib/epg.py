#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EPG 规范化：让 XMLTV 的 channel id 与播放列表的 tvg-id 严格一致。

参考项目通过 fill_erw_epg.py 修正 tvg-id 以适配外部 EPG；河北版的 EPG 是
本项目自产的，所以反过来做 —— 直接按规范化后的 tvg-id 重建 epg.xml，
4K / 超高清频道共用标清频道节目单，并合并去重。
"""
from __future__ import annotations

import gzip
from collections import defaultdict
from xml.sax.saxutils import escape, quoteattr

from .normalize import Channel


def build_epg(channels: list[Channel], source_channels: list[dict],
              programmes: list[dict], cfg: dict) -> tuple[str, bytes]:
    # 原始频道名 -> 规范化 tvg-id / tvg-name
    id_map: dict[str, str] = {}
    names: dict[str, list[str]] = defaultdict(list)
    for c in channels:
        id_map[c.raw.name] = c.tvg_id
        for n in (c.tvg_name, c.raw.name):
            if n and n not in names[c.tvg_id]:
                names[c.tvg_id].append(n)
    # EPG 里有、播放列表里没有的频道，保留原 id
    for sc in source_channels:
        sid = sc["id"]
        if sid not in id_map:
            id_map[sid] = sid
            for n in sc.get("names", []) or [sid]:
                if n and n not in names[sid]:
                    names[sid].append(n)

    # 节目重映射 + 去重（同 id 下 4K 与标清的节目会合并）
    seen: set[tuple] = set()
    mapped = []
    for p in programmes:
        cid = id_map.get(p["channel"], p["channel"])
        key = (cid, p["start"], p["stop"], p["title"])
        if key in seen:
            continue
        seen.add(key)
        mapped.append((cid, p))
    mapped.sort(key=lambda x: (x[0], x[1]["start"]))

    out = ['<?xml version="1.0" encoding="UTF-8"?>\n',
           '<tv generator-info-name="hbtv-allinone-local" generator-info-url="">\n']
    for cid in sorted(names):
        out.append('  <channel id=%s>\n' % quoteattr(cid))
        for n in names[cid]:
            out.append('    <display-name lang="zh">%s</display-name>\n' % escape(n))
        out.append('  </channel>\n')
    for cid, p in mapped:
        out.append('  <programme channel=%s start=%s stop=%s>\n'
                   % (quoteattr(cid), quoteattr(p["start"]), quoteattr(p["stop"])))
        out.append('    <title lang="zh">%s</title>\n' % escape(p["title"]))
        if p.get("desc"):
            out.append('    <desc lang="zh">%s</desc>\n' % escape(p["desc"]))
        out.append('  </programme>\n')
    out.append('</tv>\n')
    xml = "".join(out)
    return xml, gzip.compress(xml.encode("utf-8"), mtime=0)
