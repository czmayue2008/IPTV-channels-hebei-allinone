# -*- coding: utf-8 -*-
"""河北电信 IPTV 频道 / EPG 抓取器

本项目是 mytv-android/IPTV-channels 的河北电信适配版。
与原版（四川成都电信）的主要差异：

1. 认证流程不再依赖 3DES / find_key 暴力破解。河北 EPG 的
   authLoginHWCTC.jsp 会直接返回动态 userToken，用它提交
   ValidAuthenticationHWCTC.jsp 即可，config.json 里的 Authenticator
   是从机顶盒抓包得到的现成值，不需要再解密。
2. 所有接口地址由 config.json 的 AuthenticationIP 统一推导，不再写死四川 IP。
3. 频道正则兼容 ChannelURL="igmp://x|rtsp://y" 这种竖线分隔的河北格式，
   只取竖线前面的组播地址，避免生成坏 URL。
4. EPG 改用 /VSP/V3/QueryPlaybillList（JSON），替代四川的
   getTvodData.jsp + jsonBackLookStr；必须串行请求，并发会使会话失效。
5. 时移 RTSP 地址本身带查询串时，用 & 而不是 ? 追加 playseek 参数。

输出（与原项目完全一致）：
  output/hbtv.txt   DIYP 格式
  output/hbtv.m3u   m3u 格式，含 catchup-source
  output/epg.xml    XMLTV 格式
"""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from xml.sax.saxutils import escape as xmlesc

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get(
    "IPTV_CONFIG", os.path.join(ROOT, "config", "config.json")
)
OUTPUT_DIR = os.path.join(ROOT, "output")
LOG_DIR = os.path.join(ROOT, "log")
for _d in (OUTPUT_DIR, LOG_DIR):
    os.makedirs(_d, exist_ok=True)

# ---------------------------------------------------------------- 日志
logger = logging.getLogger("iptv")
logger.setLevel(logging.INFO)
_fmt = logging.Formatter(
    "%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
_fh = RotatingFileHandler(
    os.path.join(LOG_DIR, "iptv.log"), maxBytes=4 * 1024 * 1024,
    backupCount=3, encoding="utf-8",
)
_fh.setFormatter(_fmt)
_ch = logging.StreamHandler(sys.stdout)
_ch.setFormatter(_fmt)
logger.addHandler(_fh)
logger.addHandler(_ch)


# ---------------------------------------------------------------- 配置
def load_config(path=CONFIG_PATH):
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"找不到配置文件 {path}，请把 config/config.example.json 复制为 "
            f"config/config.json 并填入自己的机顶盒参数"
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


CFG = load_config()

BASE = str(CFG.get("AuthenticationIP", "")).rstrip("/")
UserID = CFG.get("UserID", "")
mac = CFG.get("mac", "")
STBID = CFG.get("STBID", "")
STBType = CFG.get("STBType", "")
STBVersion = CFG.get("STBVersion", "")
Authenticator = CFG.get("Authenticator", "")
templateName = CFG.get("templateName", "")
areaId = CFG.get("areaId", "")
RTP2HTTPD = str(CFG.get("RTP2HTTPD", "http://192.168.x.x:8888")).rstrip("/")
UserAgent = CFG.get(
    "UserAgent",
    "Mozilla/5.0 (X11; U; Linux i686; en-US) AppleWebKit/534.0 (KHTML, like Gecko)",
)
EPG_TEMPLATE = CFG.get(
    "EPGTemplate", "/EPG/jsp/hbdxpukka/en/epg/pages/tvod-player/index.html"
)
EPG_DAYS = int(CFG.get("epg_days", 2) or 2)
CONNTYPE = CFG.get("conntype", "4")
USER_FIELD = CFG.get("UserField", "2")

# 项目原版用 3DES 反推 key；河北版完全不需要，保留字段只为兼容配置页面
# key = CFG.get('key', '')

REQUIRED = {
    "AuthenticationIP": BASE,
    "UserID": UserID,
    "mac": mac,
    "STBID": STBID,
    "Authenticator": Authenticator,
}
_missing = [k for k, v in REQUIRED.items() if not v or v.startswith("<")]
if _missing:
    raise ValueError("config.json 缺少必要配置：" + "、".join(_missing))

AUTH_LOGIN_URL = BASE + "/EPG/jsp/authLoginHWCTC.jsp"
VALID_AUTH_URL = BASE + "/EPG/jsp/ValidAuthenticationHWCTC.jsp"
CHANNEL_LIST_URL = BASE + "/EPG/jsp/getchannellistHWCTC.jsp"
EPG_URL = BASE + "/VSP/V3/QueryPlaybillList"
EPG_REFERER = BASE + EPG_TEMPLATE

BASE_HEADERS = {
    "User-Agent": UserAgent,
    "Accept-Language": "zh-CN,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded",
}


# ---------------------------------------------------------------- 认证
def get_auth(max_retries=3):
    """返回 (session, user_token, jsessionid, bme_web_token)。

    河北链路：
      POST authLoginHWCTC.jsp        -> 动态 userToken / EncryptToken
      POST ValidAuthenticationHWCTC.jsp -> 会话 UserToken + JSESSIONID
    不再需要 EncryptToken 加密，也不再需要 find_key。
    """
    last_err = None
    for attempt in range(1, max_retries + 1):
        s = requests.Session()
        s.headers.update(BASE_HEADERS)
        try:
            logger.info(f"认证尝试 {attempt}/{max_retries}：{BASE}")
            r1 = s.post(
                AUTH_LOGIN_URL, data={"UserID": UserID, "VIP": ""}, timeout=12
            )
            r1.raise_for_status()
            m = re.search(
                r'document\.authform\.userToken\.value\s*=\s*"([^"]+)"', r1.text
            ) or re.search(r'var\s+EncryptToken\s*=\s*"([^"]+)"', r1.text)
            if not m:
                raise ValueError("authLoginHWCTC.jsp 未返回 userToken / EncryptToken")
            auth_token = m.group(1)
            logger.info(f"取得动态 userToken: {auth_token}")

            valid_data = {
                "UserID": UserID,
                "Lang": "1",
                "SupportHD": "1",
                "NetUserID": "",
                "Authenticator": Authenticator,
                "STBType": STBType,
                "STBVersion": STBVersion,
                "conntype": CONNTYPE,
                "STBID": STBID,
                "templateName": templateName,
                "areaId": areaId,
                "userToken": auth_token,
                "userGroupId": "1",
                "productPackageId": "",
                "mac": mac,
                "UserField": USER_FIELD,
                "SoftwareVersion": STBVersion,
                "IsSmartStb": "0",
                "desktopId": "",
                "stbmaker": "",
                "XMPPCapability": "1",
                "ChipID": "",
                "VIP": "",
            }
            r2 = s.post(
                VALID_AUTH_URL,
                data=valid_data,
                headers={"Referer": AUTH_LOGIN_URL},
                timeout=12,
            )
            r2.raise_for_status()
            um = re.search(
                r"Authentication\.CTCSetConfig\('UserToken','([^']+)'\)", r2.text
            )
            bm = re.search(
                r"Authentication\.CTCSetConfig\('BMEWebToken','([^']+)'\)", r2.text
            )
            user_token = um.group(1) if um and um.group(1) != "null" else None
            bme_token = bm.group(1) if bm and bm.group(1) != "null" else ""
            jid = s.cookies.get("JSESSIONID")
            if not user_token or not jid:
                raise ValueError("ValidAuthentication 未返回 UserToken / JSESSIONID")
            logger.info(f"认证成功：UserToken={user_token}, JSESSIONID={jid}")
            return s, user_token, jid, bme_token
        except (requests.RequestException, ValueError) as e:
            last_err = e
            logger.error(f"认证失败：{e}")
            if attempt < max_retries:
                time.sleep(3)
    raise RuntimeError(f"认证重试 {max_retries} 次仍失败：{last_err}")


# ---------------------------------------------------------------- 频道列表
# 兼容四川的 ChannelURL="igmp://x" 和河北的 ChannelURL="igmp://x|rtsp://y"
CHANNEL_PATTERN = re.compile(
    r'ChannelID\="(\d+)",'
    r'ChannelName\="(.+?)",'
    r'UserChannelID\="(\d+)",'
    r'ChannelURL=\"igmp://([^|"]+)[^"]*".+?'
    r'TimeShift\="(\d+)",'
    r'TimeShiftLength\="(\d+)".+?,'
    r'TimeShiftURL\="(.*?)".+?'
    r'FCCEnable\="(\d+)",'
    r'ChannelFCCIP=\"(.*?)\",'
    r'ChannelFCCPort=\"(.*?)\",'
    r'ChannelFECPort=\"(.*?)\"'
)


def get_channel_list(session, user_token, jsessionid):
    data = {"UserToken": user_token, "UserID": UserID, "STBID": STBID}
    headers = {
        "Cookie": f"JSESSIONID={jsessionid}",
        "Referer": VALID_AUTH_URL,
    }
    r = session.post(CHANNEL_LIST_URL, data=data, headers=headers, timeout=20)
    r.raise_for_status()
    text = r.text
    with open(os.path.join(LOG_DIR, "getchannellistHWCTC.jsp"), "w",
              encoding="utf-8") as f:
        f.write(text)
    logger.info(f"频道列表响应 {len(text)} 字符")

    channels, seen = [], set()
    for g in CHANNEL_PATTERN.finditer(text):
        cid, name, no, mcast, ts, tslen, tsurl, fcc, fccip, fccport, fecport = g.groups()
        if cid in seen or "画中画" in name or "单音轨" in name:
            continue
        seen.add(cid)
        if name == "CCTV-少儿高清":
            name = "CCTV-14高清"
        channels.append({
            "id": cid, "name": name, "no": no, "mcast": mcast,
            "timeshift": ts, "tslen": tslen, "tsurl": tsurl,
            "fcc": fcc, "fccip": fccip, "fccport": fccport, "fecport": fecport,
        })
    if not channels:
        raise RuntimeError("频道解析为 0，请检查 log/getchannellistHWCTC.jsp")
    logger.info(f"解析到 {len(channels)} 个频道")
    return channels


def category(name):
    if any(x in name for x in ("CCTV", "CHC", "CGTN")):
        return "央视频道"
    if "卫视" in name:
        return "卫视频道"
    if "专区" in name:
        return "数字频道"
    return "其他频道"


def play_urls(c):
    url = f"{RTP2HTTPD}/rtp/{c['mcast']}"
    has_q = False
    if c["fcc"] == "2" and c["fccip"] and c["fccport"]:
        url += f"?fcc={c['fccip']}:{c['fccport']}"
        has_q = True
    if c["fecport"] and c["fecport"] != "0":
        url += ("&" if has_q else "?") + f"fec={c['fecport']}"

    rtsp = None
    if c["timeshift"] == "1" and c["tsurl"]:
        # 河北 TimeShiftURL 自带 ?query，必须用 & 追加 playseek
        sep = "&" if "?" in c["tsurl"] else "?"
        rtsp = (
            c["tsurl"].replace("rtsp://", f"{RTP2HTTPD}/rtsp/", 1)
            + sep
            + "playseek=${(b)yyyyMMddHHmmss}-${(e)yyyyMMddHHmmss}"
            + f"&r2h-seek-mode=range({c['tslen']})"
        )
    return url, rtsp


def write_playlists(channels):
    groups = {"央视频道": [], "卫视频道": [], "数字频道": [], "其他频道": []}
    for c in channels:
        url, rtsp = play_urls(c)
        groups[category(c["name"])].append((c["name"], url, rtsp))

    txt_path = os.path.join(OUTPUT_DIR, "hbtv.txt")
    m3u_path = os.path.join(OUTPUT_DIR, "hbtv.m3u")
    with open(txt_path, "w", encoding="utf-8") as ft, \
         open(m3u_path, "w", encoding="utf-8") as fm:
        fm.write("#EXTM3U\n")
        for g, items in groups.items():
            ft.write(f"{g},#genre#\n")
            for name, url, rtsp in items:
                catchup = f'catchup="default" catchup-source="{rtsp}"' if rtsp else ""
                fm.write(f'#EXTINF:-1 group-title="{g}" {catchup}, {name}\n{url}\n')
                ft.write(f"{name},{rtsp}#{url}\n" if rtsp else f"{name},{url}\n")
    logger.info(f"已写入 {txt_path} / {m3u_path}，共 {len(channels)} 个频道")
    return {g: len(v) for g, v in groups.items()}


# ---------------------------------------------------------------- EPG
# 河北 EPG 会话有请求次数上限：实测连续约 1000 次请求后，服务端不再返回 JSON
# 或只返回空 playbillLites。7 天全量需要 342×N 次请求，必须定期重新认证，
# 否则后半段节目单会静默丢失（旧版把失败吞成 []，还会覆盖掉好的 epg.xml）。
EPG_REFRESH_EVERY = 300


def make_playbill_payload(channel_no, day, today, count=100, offset=0):
    """构造单频道、单天的 QueryPlaybillList 请求体。"""
    d = today + timedelta(days=day)
    return {
        "queryChannel": {"channelNOs": [channel_no]},
        "queryPlaybill": {
            "type": 0, "count": count, "offset": offset,
            "startTime": int(d.timestamp() * 1000),
            "endTime": int((d + timedelta(days=1)).timestamp() * 1000),
            "isFillProgram": 0, "mustIncluded": 0,
        },
        "needChannel": 0,
    }


def fetch_playbills(session, payload, jsessionid, bme_token):
    """返回 (playbillLites, error)。error 非空表示请求失败，需重新认证后重试。"""
    headers = {
        "User-Agent": UserAgent,
        "Content-Type": "application/json;charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Cookie": f"JSESSIONID={jsessionid}",
        "BMEWebToken": bme_token or "",
        "Referer": EPG_REFERER,
    }
    for attempt in range(2):
        try:
            r = session.post(EPG_URL, json=payload, headers=headers, timeout=12)
            r.raise_for_status()
            d = r.json()
            if d.get("result", {}).get("retCode") != "000000000":
                raise ValueError(
                    "retCode=" + str(d.get("result", {}).get("retCode"))
                )
            return d.get("channelPlaybills", [{}])[0].get("playbillLites", []) or [], ""
        except (requests.RequestException, ValueError, json.JSONDecodeError) as e:
            if attempt == 1:
                return [], "%s: %s" % (type(e).__name__, str(e)[:100])
            time.sleep(0.2)
    return [], "unknown error"


def refresh_epg_session(state):
    """重新走一遍认证，换新的 JSESSIONID / session，绕开每会话请求上限。"""
    try:
        session, _user_token, jsessionid, bme_token = get_auth()
        state.update(session=session, jsessionid=jsessionid,
                     bme_token=bme_token, since=state["requests"])
        logger.info("EPG 会话已刷新（累计 %d 次请求）" % state["requests"])
        return True
    except Exception as e:
        logger.warning("EPG 会话刷新失败：%s" % e)
        return False


def detect_available_days(state, channels, today, requested):
    """运营商通常只提供未来数天节目单（实测今天 +3 天）。

    用前 3 个频道逐天探测，连续 2 天完全没有数据就停止，避免对空日期
    发出成百上千次无效请求，也减少会话被耗尽的机会。
    """
    probes = [c["no"] for c in channels[:3]]
    if not probes:
        return requested
    good, empty_streak = 0, 0
    for day in range(requested):
        total = 0
        for no in probes:
            pb, err = fetch_playbills(
                state["session"], make_playbill_payload(no, day, today),
                state["jsessionid"], state["bme_token"])
            state["requests"] += 1
            if not err:
                total += len(pb)
        if total:
            good, empty_streak = day + 1, 0
        else:
            empty_streak += 1
            if empty_streak >= 2:
                break
    return max(1, good)


def to_records(programs):
    """[(频道名, playbillLite)] -> 可 JSON 化、可按日期归档的节目记录。"""
    out = []
    for name, p in programs:
        if not isinstance(p, dict) or "startTime" not in p:
            continue
        out.append({
            "channel": name,
            "startTime": str(p.get("startTime", "")),
            "endTime": str(p.get("endTime", "")),
            "name": p.get("name", "未知节目"),
        })
    return out


def render_epg_xml(tmp_path, final_path, channels, records, tz):
    """把窗口内所有日期的节目渲染成完整 XMLTV，成功后原子替换旧文件。"""
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<tv>\n')
        for c in channels:
            n = xmlesc(c["name"])
            f.write(f'  <channel id="{n}">\n')
            f.write(f'    <display-name lang="zh">{n}</display-name>\n')
            f.write("  </channel>\n")
        for r in sorted(records, key=lambda x: (x["startTime"], x["channel"])):
            try:
                st = datetime.fromtimestamp(int(r["startTime"]) / 1000, tz).strftime(
                    "%Y%m%d%H%M%S +0800")
                et = datetime.fromtimestamp(int(r["endTime"]) / 1000, tz).strftime(
                    "%Y%m%d%H%M%S +0800")
            except (KeyError, ValueError, TypeError):
                continue
            f.write(f'  <programme channel="{xmlesc(r["channel"])}" '
                    f'start="{st}" stop="{et}">\n')
            f.write(f'    <title lang="zh">{xmlesc(r.get("name") or "未知节目")}</title>\n')
            f.write("    <desc lang=\"zh\"></desc>\n  </programme>\n")
        f.write("</tv>\n")
    os.replace(tmp_path, final_path)


def fetch_day(state, channels, day_offset, today):
    """抓取单个日期的全部频道，返回 (records, 失败次数, 有数据次数)。"""
    records, failed, ok = [], 0, 0
    for c in channels:
        payload = make_playbill_payload(c["no"], day_offset, today)
        pb, err = fetch_playbills(
            state["session"], payload, state["jsessionid"], state["bme_token"])
        state["requests"] += 1
        if err:
            # 会话大概率已过期：换新会话再试一次
            if refresh_epg_session(state):
                pb, err = fetch_playbills(
                    state["session"], payload, state["jsessionid"], state["bme_token"])
                state["requests"] += 1
            if err:
                failed += 1
                continue
        if pb:
            ok += 1
            records.extend(to_records((c["name"], p) for p in pb))
        if state["requests"] - state["since"] >= EPG_REFRESH_EVERY:
            refresh_epg_session(state)
        time.sleep(0.02)
    return records, failed, ok


# ---------------------------------------------------------------- 滑动窗口归档
EPG_ARCHIVE_DIR = os.path.join(OUTPUT_DIR, "epg_archive")


def archive_path(date_str):
    return os.path.join(EPG_ARCHIVE_DIR, date_str + ".json")


def load_archive(date_str):
    p = archive_path(date_str)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d.get("records"), list):
            return d["records"]
    except Exception as e:
        logger.warning("EPG 归档损坏，将重新抓取 %s：%s" % (date_str, e))
    return None


def save_archive(date_str, records):
    os.makedirs(EPG_ARCHIVE_DIR, exist_ok=True)
    p, tmp = archive_path(date_str), archive_path(date_str) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({
            "date": date_str,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "count": len(records),
            "records": records,
        }, f, ensure_ascii=False)
    os.replace(tmp, p)


def prune_archives(keep_dates):
    """删除窗口外的归档：每天滚动时最旧一天被删，昨天顺延进历史。"""
    removed = 0
    if not os.path.isdir(EPG_ARCHIVE_DIR):
        return 0
    for fn in os.listdir(EPG_ARCHIVE_DIR):
        if fn.endswith(".json") and fn[:-5] not in keep_dates:
            try:
                os.remove(os.path.join(EPG_ARCHIVE_DIR, fn))
                removed += 1
            except OSError:
                pass
    return removed


def write_epg_window(session, channels, jsessionid, bme_token,
                     past_days=7, future_days=3, refresh_history=False):
    """滑动窗口 EPG：过去 past_days 天 + 今天 + 未来 future_days 天。

    每天一个归档文件。历史日期直接读归档，只有今天/未来（或缺档的历史）才
    请求接口；每次按当前日期重建窗口，自动删除最旧一天并把昨天顺延为历史。
    """
    tz = timezone(timedelta(hours=8))
    today = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    past_days = max(0, min(14, int(past_days)))
    future_days = max(1, min(7, int(future_days)))
    offsets = list(range(-past_days, future_days + 1))
    dates = [(today + timedelta(days=o)).strftime("%Y-%m-%d") for o in offsets]
    keep = set(dates)
    state = {"session": session, "jsessionid": jsessionid,
             "bme_token": bme_token, "requests": 0, "since": 0}
    os.makedirs(EPG_ARCHIVE_DIR, exist_ok=True)

    logger.info("EPG 滑动窗口：过去 %d 天 + 今天 + 未来 %d 天（历史读归档，缺档才补抓）"
                % (past_days, future_days))
    all_records, from_cache, fetched = [], 0, 0
    failed_total, ok_total = 0, 0
    for offset, date_str in zip(offsets, dates):
        if offset < 0 and not refresh_history:
            cached = load_archive(date_str)
            if cached is not None:
                all_records.extend(cached)
                from_cache += 1
                logger.info("EPG 窗口 %s（%+d 天）：归档命中 %d 条，跳过请求"
                            % (date_str, offset, len(cached)))
                continue
        recs, failed, ok = fetch_day(state, channels, offset, today)
        failed_total += failed
        ok_total += ok
        fetched += 1
        # 今天/未来的空结果不落档（可能是临时抖动），下次运行会重抓；
        # 历史日期即使为空也落档，避免反复请求运营商本来就没有的日期。
        if recs or offset < 0:
            save_archive(date_str, recs)
        all_records.extend(recs)
        logger.info("EPG 窗口 %s（%+d 天）：本次抓取 %d 条，失败 %d 次"
                    % (date_str, offset, len(recs), failed))

    attempts = fetched * len(channels)
    if attempts and failed_total > attempts * 0.2:
        raise RuntimeError(
            "EPG 窗口抓取不完整：%d/%d 次请求失败，保留上一次的 epg.xml"
            % (failed_total, attempts))
    if not all_records:
        raise RuntimeError("EPG 窗口没有任何节目，保留上一次的 epg.xml")

    path = os.path.join(OUTPUT_DIR, "epg.xml")
    render_epg_xml(path + ".tmp", path, channels, all_records, tz)
    removed = prune_archives(keep)
    logger.info(
        "EPG 窗口完成：共 %d 天（过去 %d + 今天 + 未来 %d），本次抓取 %d 天、"
        "归档命中 %d 天，%d 条节目、失败 %d 次，清理过期归档 %d 个 -> %s"
        % (len(dates), past_days, future_days, fetched, from_cache,
           len(all_records), failed_total, removed, path))
    return len(all_records)


def write_epg(session, channels, jsessionid, bme_token, days):
    """河北 EPG：/VSP/V3/QueryPlaybillList，必须串行，并发会打失效会话。

    失败不再静默吞掉：请求失败会重新认证并重试；若整体失败率过高或没有
    任何节目，直接报错并保留上一次的 epg.xml，避免用残缺数据覆盖好数据。
    """
    tz = timezone(timedelta(hours=8))
    today = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    state = {"session": session, "jsessionid": jsessionid,
             "bme_token": bme_token, "requests": 0, "since": 0}

    requested = max(1, days)
    days = detect_available_days(state, channels, today, requested)
    if days < requested:
        logger.info(
            "运营商当前只提供 %d 天节目单（请求 %d 天），按 %d 天抓取"
            % (days, requested, days))
    else:
        logger.info("EPG 可抓取天数：%d 天，共 %d 个频道" % (days, len(channels)))

    programs, ok, failed = [], 0, 0
    for idx, c in enumerate(channels, 1):
        for day in range(days):
            payload = make_playbill_payload(c["no"], day, today)
            pb, err = fetch_playbills(
                state["session"], payload,
                state["jsessionid"], state["bme_token"])
            state["requests"] += 1
            if err:
                # 会话大概率已过期：换新会话再试一次
                if refresh_epg_session(state):
                    pb, err = fetch_playbills(
                        state["session"], payload,
                        state["jsessionid"], state["bme_token"])
                    state["requests"] += 1
                if err:
                    failed += 1
                    if failed <= 5:
                        logger.warning("EPG 请求失败：%s（%s 第 %d 天）"
                                       % (err, c["name"], day + 1))
                    continue
            if pb:
                ok += 1
                programs.extend((c["name"], p) for p in pb)
            if state["requests"] - state["since"] >= EPG_REFRESH_EVERY:
                refresh_epg_session(state)
            time.sleep(0.02)
        if idx % 50 == 0:
            logger.info("EPG 进度 %d/%d，%d 条节目，失败 %d 次"
                        % (idx, len(channels), len(programs), failed))
    programs.sort(key=lambda x: (x[1].get("startTime", ""), x[0]))

    attempts = days * len(channels)
    if not programs or failed > attempts * 0.2:
        raise RuntimeError(
            "EPG 抓取不完整：%d/%d 次请求失败、仅 %d 条节目，保留上一次的 epg.xml"
            % (failed, attempts, len(programs)))

    path = os.path.join(OUTPUT_DIR, "epg.xml")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<tv>\n')
        for c in channels:
            n = xmlesc(c["name"])
            f.write(f'  <channel id="{n}">\n')
            f.write(f'    <display-name lang="zh">{n}</display-name>\n')
            f.write("  </channel>\n")
        for name, p in programs:
            try:
                st = datetime.fromtimestamp(int(p["startTime"]) / 1000, tz).strftime(
                    "%Y%m%d%H%M%S +0800"
                )
                et = datetime.fromtimestamp(int(p["endTime"]) / 1000, tz).strftime(
                    "%Y%m%d%H%M%S +0800"
                )
            except (KeyError, ValueError, TypeError):
                continue
            f.write(
                f'  <programme channel="{xmlesc(name)}" start="{st}" stop="{et}">\n'
            )
            f.write(f'    <title lang="zh">{xmlesc(p.get("name", "未知节目"))}</title>\n')
            f.write("    <desc lang=\"zh\"></desc>\n  </programme>\n")
        f.write("</tv>\n")
    os.replace(tmp, path)
    logger.info(
        "EPG 完成：%d 天 × %d 频道，%d 次有数据请求、%d 条节目、失败 %d 次 -> %s"
        % (days, len(channels), ok, len(programs), failed, path))
    return len(programs)


# ---------------------------------------------------------------- 可选：同步 EPG 到 Git
def sync_epg():
    repo = CFG.get("epg_git_repo")
    token = CFG.get("epg_git_private_token")
    if not repo or not token:
        logger.info("未配置 EPG Git 仓库，跳过同步")
        return
    if not repo.startswith("https://"):
        logger.error("epg_git_repo 仅支持 HTTPS 地址")
        return
    repo_url = repo.replace("https://", f"https://{token}@", 1)
    git_dir = os.path.join(ROOT, "epg_repo")
    try:
        if os.path.exists(git_dir):
            shutil.rmtree(git_dir)
        subprocess.run(["git", "clone", repo_url, git_dir], check=True,
                       capture_output=True)
        subprocess.run(["git", "config", "user.name", "iptv-bot"],
                       cwd=git_dir, check=True)
        subprocess.run(["git", "config", "user.email", "iptv-bot@local"],
                       cwd=git_dir, check=True)
        src = os.path.join(OUTPUT_DIR, "epg.xml")
        if not os.path.exists(src):
            return
        shutil.copy2(src, os.path.join(git_dir, "epg.xml"))
        subprocess.run(["git", "add", "."], cwd=git_dir, check=True)
        subprocess.run(
            ["git", "commit", "-m", f"Update EPG {datetime.now():%Y-%m-%d %H:%M:%S}"],
            cwd=git_dir, check=True, capture_output=True,
        )
        subprocess.run(["git", "push", repo_url, "-f"], cwd=git_dir,
                       check=True, capture_output=True)
        logger.info("EPG 同步成功")
    except subprocess.CalledProcessError as e:
        msg = (e.stderr or b"").decode("utf-8", "replace").replace(token, "******")
        logger.error(f"EPG 同步失败：{msg}")
    except Exception as e:
        logger.error(f"EPG 同步失败：{e}")
    finally:
        if os.path.exists(git_dir):
            shutil.rmtree(git_dir, ignore_errors=True)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="河北电信 IPTV 频道 / EPG 抓取")
    ap.add_argument("--epg-days", type=int, default=EPG_DAYS,
                    help="抓取 EPG 天数，默认取 config.json 的 epg_days（默认 2）")
    ap.add_argument("--skip-epg", action="store_true", help="只更新播放列表")
    ap.add_argument("--no-sync", action="store_true", help="不同步 EPG 到 Git 仓库")
    ap.add_argument("--epg-window", action="store_true",
                    help="滑动窗口：过去 N 天 + 今天 + 未来 M 天，按天归档滚动")
    ap.add_argument("--epg-past-days", type=int, default=7,
                    help="窗口保留过去几天（默认 7，最大 14）")
    ap.add_argument("--epg-future-days", type=int, default=3,
                    help="窗口保留未来几天（默认 3，最大 7）")
    ap.add_argument("--refresh-history", action="store_true",
                    help="强制重抓历史日期（默认直接读归档）")
    a = ap.parse_args()

    session, user_token, jid, bme = get_auth()
    channels = get_channel_list(session, user_token, jid)
    stats = write_playlists(channels)
    logger.info(f"频道分组：{stats}")
    if not a.skip_epg:
        try:
            if a.epg_window:
                write_epg_window(session, channels, jid, bme,
                                 a.epg_past_days, a.epg_future_days,
                                 a.refresh_history)
            else:
                write_epg(session, channels, jid, bme, max(1, a.epg_days))
        except RuntimeError as e:
            # 让 Web 后台任务能明确判失败，而不是拿残缺节目单继续整理
            logger.error("EPG 抓取失败：%s" % e)
            sys.exit(1)
    if not a.no_sync:
        sync_epg()
    logger.info("全部完成：output/hbtv.txt、output/hbtv.m3u、output/epg.xml")


if __name__ == "__main__":
    main()
