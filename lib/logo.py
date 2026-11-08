#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""台标匹配：对应 iptv-cd-telecom/home.py 的 generateHome() 补齐 icon 逻辑。

按 tvg-id → tvg-name → 原始名 → 别名表 逐级匹配，并兼容空格 / 大小写 /
画质后缀差异。台标文件来自参考项目 logo/ 目录，可随时增删，程序自动重建索引。
"""
from __future__ import annotations

import os
import re

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")


class LogoIndex:
    def __init__(self, logo_dir: str, aliases: dict | None = None):
        self.dir = logo_dir
        self.aliases = aliases or {}
        self.files: dict[str, str] = {}
        self._index: dict[str, str] = {}
        self._rebuild()

    @staticmethod
    def _key(s: str) -> str:
        return re.sub(r"[\s\-_]", "", s).lower()

    def _rebuild(self):
        if not os.path.isdir(self.dir):
            return
        for fn in sorted(os.listdir(self.dir)):
            stem, ext = os.path.splitext(fn)
            if ext.lower() not in IMG_EXT:
                continue
            self.files[stem] = fn
            self._index.setdefault(self._key(stem), fn)

    def lookup(self, tvg_id: str, tvg_name: str, raw_name: str) -> str:
        candidates = [
            tvg_id, tvg_name, raw_name,
            self.aliases.get(raw_name, ""),
            self.aliases.get(tvg_name, ""),
            self.aliases.get(tvg_id, ""),
        ]
        # 去掉画质词再试一次（如「第一剧场高清」→「第一剧场」）
        expanded = []
        for c in candidates:
            if not c:
                continue
            expanded.append(c)
            stripped = re.sub(r"(超高清|高清|标清|4[Kk])", "", c).strip()
            if stripped and stripped != c:
                expanded.append(stripped)
        for c in expanded:
            if c in self.files:
                return self.files[c]
            hit = self._index.get(self._key(c))
            if hit:
                return hit
        return ""

    def count(self) -> int:
        return len(self.files)
