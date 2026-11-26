#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""频道整理层：规范化、分组、去重、画质冲突处理。

对应 iptv-cd-telecom/script/home.py 的 filterCategory / 名称清洗 / checkChannelExist，
但做了两处针对河北数据的修正：

1. 河北同时存在标清 / 4K / 超高清版本（如「青海卫视」与「青海卫视高清」、
   「北京卫视4K」与「北京卫视超高清」）。原项目直接去画质词会撞名并丢频道。
   这里把 tvg-id 统一到标清基础名（共用 EPG 与台标），tvg-name 仅在撞名时
   保留画质词，从而一个频道都不丢。
2. 分组规则从「四川本地」改为面向河北数据的完整分类树：央视 / 卫视 /
   河北本地（省市）/ 河北县级（正则兜底）/ 少儿 / 影视 / 体育休闲 /
   纪实文化 / 生活时尚 / 购物 / 测试与直播室 / 其他（兜底）。
   规则支持 keywords 子串与 regex 正则两种匹配，空规则作为兜底组，
   不再沿用源 m3u 的 group-title（源里绝大多数频道都标着「其他频道」）。
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from .source import RawChannel


@dataclass
class Channel:
    raw: RawChannel
    tvg_id: str                    # EPG / 台标用的规范 id（4K 映射到标清）
    tvg_name: str                  # 播放器匹配用的规范名（撞名时带画质词）
    group: str
    logo: str = ""                 # 台标文件名，空表示无
    is_4k: bool = False
    index: int = 0                 # 源顺序
    catchup_days: int = 0

    @property
    def name(self) -> str:
        return self.tvg_name


def halfwidth(text: str) -> str:
    """全角转半角（iptv-cd-telecom/script/fill_m3u8.py 同款实现）。"""
    out = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:
            code = 0x20
        elif 0xFF01 <= code <= 0xFF5E:
            code -= 0xFEE0
        out.append(chr(code))
    return "".join(out)


def clean_name(name: str, cfg: dict) -> str:
    """规范化显示名：去画质词、去连接符。"""
    n = cfg.get("normalize", {})
    s = halfwidth(name) if n.get("fullwidth_to_halfwidth", True) else name
    for w in n.get("strip_hd_words", ["超高清", "高清", "标清"]):
        s = s.replace(w, "")
    for c in n.get("strip_chars", ["-"]):
        s = s.replace(c, "")
    return s.strip()


def full_name(name: str, cfg: dict) -> str:
    """保留画质词的版本，用于同基础名频道之间的区分。"""
    n = cfg.get("normalize", {})
    s = halfwidth(name) if n.get("fullwidth_to_halfwidth", True) else name
    for c in n.get("strip_chars", ["-"]):
        s = s.replace(c, "")
    return s.strip()


def base_id(name: str, cfg: dict) -> str:
    """EPG / 台标 id：在 clean_name 基础上再去掉 4K，映射到标清频道。"""
    s = clean_name(name, cfg)
    if cfg.get("normalize", {}).get("map_4k_to_sd_id", True):
        stripped = re.sub(r"4[Kk]", "", s).strip()
        # 去掉 4K 后失去意义时（如 CCTV-4K）保留原样
        if stripped and len(stripped) >= 2 and not re.fullmatch(r"CCTV", stripped):
            s = stripped
    return s or clean_name(name, cfg)


def is_4k(name: str) -> bool:
    return "4K" in name.upper() or "超高清" in name or "UHD" in name.upper()


def classify(name: str, raw_group: str, cfg: dict) -> str:
    """把频道归入分组。

    规则按 groups 数组顺序匹配，先中先得。每条规则支持两种匹配方式：

    - ``keywords``：子串匹配（原有行为，大小写不敏感）；
    - ``regex``：正则匹配，用于无法逐一枚举的成批频道
      （如河北县级台 ``XX电视 / XX综合 / XX新闻综合 / XX综合频道``）。

    ``keywords`` 与 ``regex`` 均为空的规则视为**兜底组**：所有规则都不中时
    归入该组，而不是沿用源 m3u 的 group-title（源里 260+ 个频道都标着
    「其他频道」，还会漏「数字频道」这类零散分组，导致分组泄漏）。
    若没有空规则，则退回旧行为（raw_group 或「其他频道」）。
    """
    fallback = raw_group or "其他频道"
    for rule in cfg.get("groups", []):
        kws = rule.get("keywords") or []
        rx = rule.get("regex") or ""
        if kws and _match_any(name, kws):
            return rule["name"]
        if rx and re.search(rx, name):
            return rule["name"]
        if not kws and not rx:
            fallback = rule["name"]
    return fallback


def _match_any(name: str, words: list[str]) -> bool:
    low = name.lower()
    return any(w.strip().lower() in low for w in words if w.strip())


def build_channels(raw: list[RawChannel], cfg: dict, logo_lookup=None) -> list[Channel]:
    """生成整理后的频道列表。logo_lookup(tvg_id, tvg_name, raw_name) -> 文件名。"""
    exclude = cfg.get("exclude_keywords") or []
    include = cfg.get("include_keywords") or []

    # 第一遍：计算基础名、过滤
    staged = []
    for i, r in enumerate(raw):
        if exclude and _match_any(r.name, exclude):
            continue
        if include and not _match_any(r.name, include):
            continue
        staged.append({
            "raw": r,
            "index": i,
            "base": base_id(r.name, cfg),
            "clean": clean_name(r.name, cfg) or full_name(r.name, cfg),
            "full": full_name(r.name, cfg) or r.name,
        })

    # 第二遍：同 base 的频道保留画质词以区分（不丢频道）
    by_base: dict[str, list[dict]] = defaultdict(list)
    for item in staged:
        by_base[item["base"]].append(item)
    for base, items in by_base.items():
        for item in items:
            item["tvg_name"] = item["clean"] if len(items) == 1 else item["full"]

    # 第三遍：极端情况下仍重名则追加序号
    used: dict[str, int] = defaultdict(int)
    for item in staged:
        n = item["tvg_name"]
        used[n] += 1
        if used[n] > 1:
            item["tvg_name"] = "%s #%d" % (n, used[n])

    # 第四遍：分组、台标、时移天数
    channels: list[Channel] = []
    for item in staged:
        r: RawChannel = item["raw"]
        tid = item["base"]
        tname = item["tvg_name"]
        logo = ""
        if logo_lookup:
            logo = logo_lookup(tid, tname, r.name) or ""
        channels.append(Channel(
            raw=r,
            tvg_id=tid,
            tvg_name=tname,
            group=classify(r.name, r.group, cfg),
            logo=logo,
            is_4k=is_4k(r.name),
            index=item["index"],
            catchup_days=r.catchup_days,
        ))

    # 按分组顺序输出（与参考项目 generateM3U8 一致），组内保持源频道号顺序
    if cfg.get("sort_by_group", True):
        order = {g["name"]: i for i, g in enumerate(cfg.get("groups", []))}
        channels.sort(key=lambda c: (order.get(c.group, len(order)), c.index))
    return channels
