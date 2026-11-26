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
    def __init__(self, logo_dir: str, aliases: dict | None = None,
                 extra_dirs: list[str] | None = None):
        """logo_dir 是镜像自带的台标库；extra_dirs 是用户上传的台标目录，优先级更高。

        多个目录里有同名文件时，排在 extra_dirs 前面的生效，其次才是内置库。
        """
        self.dir = logo_dir
        self.dirs = [d for d in (extra_dirs or []) if d] + [logo_dir]
        self.aliases = aliases or {}
        self.files: dict[str, str] = {}
        self._index: dict[str, str] = {}
        self._rebuild()

    @staticmethod
    def _key(s: str) -> str:
        return re.sub(r"[\s\-_]", "", s).lower()

    def _rebuild(self):
        # files / _index 的值都是「文件名」而不是路径：lookup() 的返回值会直接
        # 拼进播放列表的台标网址。多目录同名时，先遍历的目录（用户上传）优先。
        self.files, self._index = {}, {}
        for d in self.dirs:
            if not os.path.isdir(d):
                continue
            for fn in sorted(os.listdir(d)):
                stem, ext = os.path.splitext(fn)
                if ext.lower() not in IMG_EXT or not os.path.isfile(os.path.join(d, fn)):
                    continue
                # 用 setdefault 而不是直接赋值：同一频道名在内置库与上传目录里都
                # 存在（哪怕扩展名不同，如 CCTV1.png / CCTV1.jpg）时，先遍历的
                # 上传目录必须保留，否则上传的台标会被内置库覆盖回去。
                self.files.setdefault(stem, fn)
                self._index.setdefault(self._key(stem), fn)

    def lookup(self, tvg_id: str, tvg_name: str, raw_name: str) -> str:
        """返回台标文件名（播放列表用），找不到返回空串。"""
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
