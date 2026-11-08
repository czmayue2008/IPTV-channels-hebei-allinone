#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读输入适配器。

把 IPTV-channels-hebei 项目 output/ 下的 hbtv.m3u 与 epg.xml 解析成中间结构。
这一层是「融合」的唯一接缝：换其它数据源时，只要提供同样结构即可。
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
MULTICAST_RE = re.compile(r'/(?:rtp|udp)/(239\.\d+\.\d+\.\d+:\d+)')
FCC_RE = re.compile(r'[?&]fcc=([^&]+)')
FEC_RE = re.compile(r'[?&]fec=([^&]+)')
RANGE_RE = re.compile(r'r2h-seek-mode=range\((\d+)\)')


@dataclass
class RawChannel:
    name: str                      # 原始频道名
    group: str                     # 原分组
    url: str                       # 主播放地址（组播代理）
    catchup_source: str = ""       # 时移/回看源（rtp2httpd 包装过的 RTSP）
    catchup: str = ""              # 源里的 catchup 属性
    attrs: dict = field(default_factory=dict)
    multicast: str = ""            # 239.x.x.x:port
    fcc: str = ""
    fec: str = ""
    seek_seconds: int = 0          # 时移窗口（秒）

    @property
    def catchup_days(self) -> int:
        if not self.catchup_source:
            return 0
        return max(1, (self.seek_seconds + 86399) // 86400) if self.seek_seconds else 1


def _display_name(line: str) -> str:
    """EXTINF 行末尾的显示名。属性里可能有逗号，所以取最后一个引号后的内容。"""
    i = line.rfind('",')
    if i >= 0:
        return line[i + 2:].strip()
    if "," in line:
        return line.split(",", 1)[1].strip()
    return ""


def parse_m3u(path: str) -> list[RawChannel]:
    if not os.path.exists(path):
        raise FileNotFoundError("找不到播放列表：%s\n请先运行现有 IPTV-channels-hebei 项目生成 hbtv.m3u" % path)

    out: list[RawChannel] = []
    cur: dict | None = None
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\r\n")
            if line.startswith("#EXTINF"):
                cur = {
                    "attrs": dict(ATTR_RE.findall(line)),
                    "name": _display_name(line),
                }
            elif cur is not None and line and not line.startswith("#"):
                a = cur["attrs"]
                url = line.strip()
                m = MULTICAST_RE.search(url)
                fc = FCC_RE.search(url)
                fe = FEC_RE.search(url)
                cs = a.get("catchup-source", "")
                rg = RANGE_RE.search(cs)
                out.append(RawChannel(
                    name=cur["name"],
                    group=a.get("group-title", "其他频道"),
                    url=url,
                    catchup_source=cs,
                    catchup=a.get("catchup", ""),
                    attrs=a,
                    multicast=m.group(1) if m else "",
                    fcc=fc.group(1) if fc else "",
                    fec=fe.group(1) if fe else "",
                    seek_seconds=int(rg.group(1)) if rg else 0,
                ))
                cur = None
    return out


def parse_epg(path: str) -> tuple[list[dict], list[dict]]:
    """返回 (channels, programmes)。channels: {id, names}; programmes: {channel,start,stop,title,desc}"""
    if not os.path.exists(path):
        return [], []
    root = ET.parse(path).getroot()
    channels = []
    for c in root.findall("channel"):
        channels.append({
            "id": c.get("id", ""),
            "names": [(d.text or "") for d in c.findall("display-name")],
        })
    programmes = []
    for p in root.findall("programme"):
        title = p.findtext("title") or ""
        desc = p.findtext("desc") or ""
        programmes.append({
            "channel": p.get("channel", ""),
            "start": p.get("start", ""),
            "stop": p.get("stop", ""),
            "title": title,
            "desc": desc,
        })
    return channels, programmes
