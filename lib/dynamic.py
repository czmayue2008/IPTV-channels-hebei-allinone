#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""播放列表的动态改写（移植自 hbtv-playlist/serve.py 与参考项目的动态函数）。

两点为纯内网场景做了增强：
1. apply_params 支持 URL 参数即时过滤 / 换代理 / 换时移模板；
2. absolutize 把根相对的台标与 EPG 地址补成当前访问主机的绝对地址，
   这样用任意内网 IP 或域名订阅，播放器都能取到台标与节目单，且生成物
   本身不含任何写死的主机地址。
"""
from __future__ import annotations

import re

from .exporters import APTV_SEEK, KODI_SEEK, _set_param, replace_origin

LOGO_ATTR_RE = re.compile(r'(tvg-logo=")(/[^"]*)(")')
EPG_ATTR_RE = re.compile(r'((?:x-tvg-url|url-tvg)=")(/[^"]*)(")')
LOGO_NAME_RE = re.compile(r'(tvg-logo=")([^"]*)(")')
TVG_NAME_RE = re.compile(r'tvg-name="([^"]*)"')
CATCHUP_RTSP_RE = re.compile(r'(catchup-source=")https?://[^/]+/rtsp/')


def apply_params(text: str, q: dict) -> str:
    """q 为 parse_qs 结果；只对 m3u/m3u8 的行结构生效。"""
    def one(key):
        v = q.get(key)
        return v[0] if v else ""

    if one("aptv"):
        text = text.replace(KODI_SEEK, APTV_SEEK)
    if one("epg"):
        text = re.sub(r'((?:x-tvg-url|url-tvg)=")([^"]*)(")',
                      lambda m: m.group(1) + one("epg") + m.group(3), text)
    if one("logo"):
        base = one("logo").rstrip("/")

        def fix_logo(m):
            fn = m.group(2).rsplit("/", 1)[-1]
            return m.group(1) + (base + "/" + fn if fn else "") + m.group(3)
        text = LOGO_NAME_RE.sub(fix_logo, text)
    if one("rtspProxy"):
        text = CATCHUP_RTSP_RE.sub(
            lambda m: m.group(1) + one("rtspProxy").rstrip("/") + "/rtsp/", text)
    if one("r2hToken"):
        token = one("r2hToken")
        text = re.sub(r'(catchup-source=")([^"]*)(")',
                      lambda m: m.group(1) + _set_param(m.group(2), "r2h-token", token) + m.group(3),
                      text)

    proxy, fcc = one("proxy"), one("fcc")
    exclude = [x.strip().lower() for x in one("exclude").split(",") if x.strip()]
    include = [x.strip().lower() for x in one("include").split(",") if x.strip()]

    def keep(name: str) -> bool:
        n = name.lower()
        if exclude and any(k in n for k in exclude):
            return False
        if include and not any(k in n for k in include):
            return False
        return True

    out, pending, drop = [], [], False
    for line in text.split("\n"):
        if line.startswith("#KODIPROP"):
            pending.append(line)
        elif line.startswith("#EXTINF"):
            m = TVG_NAME_RE.search(line)
            drop = not keep(m.group(1) if m else "")
            if not drop:
                out.extend(pending)
                out.append(line)
            pending = []
        elif line.startswith("#"):
            if not drop:
                out.append(line)
        else:
            # 只有「整行就是一个 URL」的 m3u8 才能安全改主机；
            # DIYP 的「名称,URL」行保持原样，避免把中文名当 URL 解析。
            if line and line.startswith(("http://", "https://")):
                if proxy:
                    line = replace_origin(line, proxy)
                if fcc:
                    line = _set_param(line, "fcc", fcc)
            if not drop:
                out.append(line)
            drop = False
    return "\n".join(out)


def absolutize(text: str, base: str) -> str:
    """把根相对地址补成绝对地址。base 形如 http://192.168.1.10:5000"""
    base = base.rstrip("/")
    if not base:
        return text
    text = LOGO_ATTR_RE.sub(lambda m: m.group(1) + base + m.group(2) + m.group(3), text)
    text = EPG_ATTR_RE.sub(lambda m: m.group(1) + base + m.group(2) + m.group(3), text)
    return text
