#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HBTV All-in-One：抓取 + 整理 + 内网订阅，单一 Flask 应用。

由 IPTV-channels-hebei（河北电信抓取）与 hbtv-playlist（多播放器整理层）
合并而成。全部资源（页面、台标、播放列表、EPG）都由本服务在内网提供，
运行时不请求任何外部地址。定时任务使用内置线程调度，不依赖系统 crontab，
Windows / Linux / Docker 均可运行，不读写系统定时任务。
"""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import parse_qs, quote, unquote

from flask import (Flask, Response, jsonify, redirect, render_template,
                   request, send_from_directory, session)
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import organize                                   # noqa: E402
from lib.dynamic import absolutize, apply_params   # noqa: E402

CONFIG_PATH = os.path.join(ROOT, "config", "config.json")
ORGANIZE_CONFIG = organize.ORGANIZE_CONFIG
OUTPUT_DIR = os.path.join(ROOT, "output")
PLAYLIST_DIR = os.path.join(OUTPUT_DIR, "playlists")
LOGO_DIR = os.path.join(ROOT, "logo")
FETCH_SCRIPT = os.path.join(ROOT, "get_iptv_channels_V1.1.py")

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.json.ensure_ascii = False

# 密码哈希、会话密钥、订阅令牌只落在本地 config/auth.json，不入库、不进镜像。
# HBTV_AUTH_FILE 仅供自检脚本隔离真实凭据；HBTV_PASSWORD 供容器首次启动预设。
AUTH_PATH = os.environ.get("HBTV_AUTH_FILE") or os.path.join(ROOT, "config", "auth.json")
SESSION_DAYS = 7
MAX_FAILS, LOCK_SECONDS = 5, 60
MEDIA_PREFIXES = ("/playlists", "/logo/", "/output/")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=SESSION_DAYS),
)
app.secret_key = os.environ.get("HBTV_SECRET_KEY") or secrets.token_hex(32)

os.makedirs(PLAYLIST_DIR, exist_ok=True)


@app.after_request
def _cors(resp):
    # 仅订阅资源需要跨域（播放器 / 电视盒子）；控制台 API 不开放 CORS，
    # 避免任意站点借浏览器读取管理接口。
    if request.path.startswith(MEDIA_PREFIXES):
        resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ts_str(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""


# --------------------------------------------------------------------------
# 登录认证
# --------------------------------------------------------------------------
_LOGIN_FAILS: dict[str, list] = {}
AUTH_FREE = {"/login", "/logout", "/health", "/favicon.ico",
             "/api/auth/status", "/api/auth/login", "/api/auth/setup"}


def load_auth() -> dict:
    """每次读取，保证在控制台改密码 / 换令牌后立即生效。"""
    try:
        with open(AUTH_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and d.get("password_hash"):
            return d
    except (OSError, ValueError):
        pass
    return {}


def save_auth(d: dict) -> None:
    os.makedirs(os.path.dirname(AUTH_PATH) or ".", exist_ok=True)
    tmp = AUTH_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, AUTH_PATH)
    if d.get("secret_key"):
        app.secret_key = d["secret_key"]


def create_auth(password: str) -> dict:
    """设置 / 修改密码；保留会话密钥与订阅令牌，避免把已登录用户踢下线。"""
    d = load_auth()
    d.update(
        username="admin",
        password_hash=generate_password_hash(password, method="pbkdf2:sha256:260000"),
        secret_key=d.get("secret_key") or secrets.token_hex(32),
        subscribe_token=d.get("subscribe_token") or secrets.token_urlsafe(24),
        protect_media=bool(d.get("protect_media", False)),
        created_at=d.get("created_at") or now_str(),
        updated_at=now_str(),
    )
    save_auth(d)
    return d


def is_logged_in() -> bool:
    return bool(session.get("hbtv_user")) and bool(load_auth().get("password_hash"))


def media_token_ok() -> bool:
    d = load_auth()
    want = d.get("subscribe_token", "")
    if not want:
        return False
    got = request.args.get("token") or request.headers.get("X-Subscribe-Token") or ""
    auth = request.headers.get("Authorization", "")
    if not got and auth.startswith("Bearer "):
        got = auth[7:].strip()
    return bool(got) and hmac.compare_digest(str(got), str(want))


@app.before_request
def auth_guard():
    """控制台与所有管理 API 强制登录；播放列表 / 台标 / EPG 按开关决定是否要令牌。"""
    if request.method == "OPTIONS":
        return None
    p = request.path
    if p.startswith("/static/") or p in AUTH_FREE:
        return None
    d = load_auth()

    # 尚未设置密码：只放行初始化相关接口，其它页面一律引导到登录页
    if not d.get("password_hash"):
        if p.startswith("/api/auth/"):
            return None
        if p.startswith(MEDIA_PREFIXES):
            return None
        if p.startswith("/api/"):
            return jsonify({"success": False, "message": "尚未设置管理员密码",
                            "setup_required": True}), 401
        return redirect("/login")

    if p.startswith(MEDIA_PREFIXES):
        if not d.get("protect_media") or is_logged_in() or media_token_ok():
            return None
        return Response("订阅地址需要有效令牌，请在控制台复制带令牌的订阅地址",
                        status=401, mimetype="text/plain; charset=utf-8")

    if is_logged_in():
        return None
    if p.startswith("/api/"):
        return jsonify({"success": False, "message": "未登录或会话已过期",
                        "auth_required": True}), 401
    return redirect("/login?next=" + quote(request.full_path.rstrip("?")))


_env_pw = os.environ.get("HBTV_PASSWORD", "")
if _env_pw and not load_auth():
    create_auth(_env_pw)
    print("[auth] 已按 HBTV_PASSWORD 环境变量初始化管理员密码")
elif load_auth().get("secret_key"):
    app.secret_key = load_auth()["secret_key"]


# --------------------------------------------------------------------------
# 任务状态（手动整理 / 定时整理共用）
# --------------------------------------------------------------------------
JOB_LOCK = threading.Lock()
STATE = {
    "running": False,
    "mode": "",
    "trigger": "",
    "started_at": "",
    "finished_at": "",
    "ok": None,
    "error": "",
    "files": [],
    "last_result": {},
    "logs": [],
}


def log(text: str, level: str = "info"):
    with JOB_LOCK:
        STATE["logs"].append({
            "i": len(STATE["logs"]),
            "t": datetime.now().strftime("%H:%M:%S"),
            "text": str(text),
            "level": level,
        })
        if len(STATE["logs"]) > 1500:
            del STATE["logs"][:500]
    print("[%s] %s" % (level, text), flush=True)


def _fetch(epg_days: int, past_days: int | None = None,
           epg_window: bool | None = None) -> bool:
    """重新抓取频道与 EPG。--no-sync 确保不会请求外部 Git 仓库。

    启用回看窗口时走 --epg-window：epg_days 是「未来天数」，past_days 是
    「过去天数」；历史日期由按天归档提供，只有缺档才回补。
    """
    try:
        sched = read_schedule()
    except Exception:
        sched = {}
    window = (bool(epg_window) if epg_window is not None
              else bool(sched.get("epg_window", True)))
    cmd = [sys.executable, FETCH_SCRIPT, "--no-sync"]
    if window:
        past = past_days if past_days is not None else int(sched.get("epg_past_days", 7))
        cmd += ["--epg-window", "--epg-past-days", str(past),
                "--epg-future-days", str(epg_days)]
        log("抓取 EPG 滑动窗口：过去 %d 天 + 今天 + 未来 %d 天（历史读归档，缺档补抓）"
            % (past, epg_days))
    else:
        cmd += ["--epg-days", str(epg_days)]
        log("抓取频道与 EPG：epg-days=%d（--no-sync，纯内网）" % epg_days)
    env = dict(os.environ)
    env.update({"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
    p = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", env=env)
    for line in p.stdout:
        line = line.rstrip("\r\n")
        if line:
            log(line)
    rc = p.wait()
    log("抓取结束，退出码 %d" % rc, "info" if rc == 0 else "error")
    return rc == 0


def _worker(mode: str, trigger: str, epg_days: int | None = None,
            epg_past_days: int | None = None, epg_window: bool | None = None):
    ok, err = True, ""
    try:
        sched = read_schedule()
        if mode == "full":
            days = epg_days if epg_days is not None else int(sched.get("epg_days", 2))
            if not _fetch(days, epg_past_days, epg_window):
                raise RuntimeError("抓取失败，本次不整理（源数据保持上一次结果）")
        cfg = organize.load_config()
        manifest = organize.run(cfg, emit=lambda s: log(s))
        with JOB_LOCK:
            STATE["files"] = manifest["files"]
            STATE["last_result"] = manifest
        log("整理完成：共生成 %d 个播放列表文件" % len(manifest["playlists"]), "ok")
    except Exception as e:
        ok, err = False, str(e)
        log("任务失败：%s" % err, "error")
    finally:
        with JOB_LOCK:
            STATE.update(running=False, finished_at=now_str(), ok=ok, error=err)


def start_job(mode: str = "organize", trigger: str = "manual",
              epg_days: int | None = None,
              epg_past_days: int | None = None,
              epg_window: bool | None = None) -> tuple[bool, str]:
    """手动「抓取并整理」可临时覆盖未来/过去天数；定时任务用 schedule 配置。"""
    if mode not in ("organize", "full"):
        return False, "未知任务类型"
    if epg_days is not None and not 1 <= epg_days <= 7:
        return False, "EPG 未来天数需在 1~7 之间"
    if epg_past_days is not None and not 0 <= epg_past_days <= 14:
        return False, "EPG 回看天数需在 0~14 之间"
    with JOB_LOCK:
        if STATE["running"]:
            return False, "已有任务正在运行，请稍候"
        STATE.update(running=True, mode=mode, trigger=trigger,
                     started_at=now_str(), finished_at="", ok=None,
                     error="", files=[], logs=[])
    log("任务启动：%s（%s%s）" % (
        "抓取并整理" if mode == "full" else "仅整理",
        "定时" if trigger == "schedule" else "手动",
        "，未来 %s 天 / 回看 %s 天" % (epg_days, epg_past_days)
        if mode == "full" and (epg_days or epg_past_days is not None) else ""))
    threading.Thread(target=_worker,
                     args=(mode, trigger, epg_days, epg_past_days, epg_window),
                     daemon=True).start()
    return True, "任务已启动"


# --------------------------------------------------------------------------
# 定时整理（内置调度，跨平台）
# --------------------------------------------------------------------------
SCHED_LOCK = threading.Lock()
_next_run: list[float | None] = [None]


def read_organize_config() -> dict:
    with open(ORGANIZE_CONFIG, "r", encoding="utf-8") as f:
        return json.load(f)


def read_schedule() -> dict:
    try:
        return read_organize_config().get("schedule", {})
    except Exception:
        return {}


def compute_next(sched: dict, from_ts: float | None = None) -> float | None:
    if not sched.get("enabled"):
        return None
    now = from_ts if from_ts is not None else time.time()
    if sched.get("mode") == "daily":
        m = re.match(r"^(\d{1,2}):(\d{2})$", str(sched.get("daily_time", "04:30")))
        hh, mm = (int(m.group(1)), int(m.group(2))) if m else (4, 30)
        hh, mm = min(23, max(0, hh)), min(59, max(0, mm))
        d = datetime.fromtimestamp(now).replace(hour=hh, minute=mm,
                                                second=0, microsecond=0)
        if d.timestamp() <= now:
            d += timedelta(days=1)
        return d.timestamp()
    minutes = int(sched.get("interval_minutes", 360) or 360)
    return now + max(1, minutes) * 60


def _scheduler_loop():
    while True:
        time.sleep(5)
        try:
            sched = read_schedule()
            with SCHED_LOCK:
                nxt = _next_run[0]
            if not sched.get("enabled"):
                if nxt is not None:
                    with SCHED_LOCK:
                        _next_run[0] = None
                continue
            if nxt is None:
                nxt = compute_next(sched)
                with SCHED_LOCK:
                    _next_run[0] = nxt
                log("定时整理已启用，下次运行：%s" % ts_str(nxt))
                continue
            if time.time() >= nxt:
                # 先算好下一次，避免任务运行期间重复触发
                with SCHED_LOCK:
                    _next_run[0] = compute_next(sched)
                mode = "full" if sched.get("run_fetch", True) else "organize"
                ok, msg = start_job(mode, trigger="schedule")
                if not ok:
                    log("定时整理跳过：%s" % msg, "warn")
        except Exception as e:
            log("调度器异常：%s" % e, "error")


def start_background_services():
    threading.Thread(target=_scheduler_loop, daemon=True).start()
    sched = read_schedule()
    if sched.get("enabled") and sched.get("run_at_startup"):
        def _boot():
            time.sleep(8)
            mode = "full" if sched.get("run_fetch", True) else "organize"
            start_job(mode, trigger="startup")
        threading.Thread(target=_boot, daemon=True).start()


# --------------------------------------------------------------------------
# 页面与静态资源（全部内网）
# --------------------------------------------------------------------------
def parse_channels():
    """解析 output/hbtv.txt，用于首页频道预览。"""
    channels = []
    path = os.path.join(OUTPUT_DIR, "hbtv.txt")
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "#genre#" in line:
                    continue
                left, multicast = (line.split("#", 1) + [""])[:2] if "#" in line else (line, "")
                if "," not in left:
                    continue
                name, rtsp = left.split(",", 1)
                channels.append({"name": name.strip(), "rtsp_url": rtsp.strip(),
                                 "multicast_url": multicast.strip()})
    except FileNotFoundError:
        pass
    return channels


# --------------------------------------------------------------------------
# 登录 / 密码 / 订阅令牌
# --------------------------------------------------------------------------
def _login_blocked(ip: str) -> int:
    rec = _LOGIN_FAILS.get(ip)
    if not rec:
        return 0
    count, until = rec
    if until and until > time.time():
        return max(1, int(until - time.time()))
    if until:
        _LOGIN_FAILS.pop(ip, None)
    return 0


def _login_failed(ip: str) -> None:
    rec = _LOGIN_FAILS.get(ip) or [0, 0.0]
    rec[0] += 1
    if rec[0] >= MAX_FAILS:
        rec[0], rec[1] = 0, time.time() + LOCK_SECONDS
    _LOGIN_FAILS[ip] = rec


@app.route("/login")
def login_page():
    if is_logged_in():
        return redirect("/")
    nxt = request.args.get("next", "/")
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"
    return render_template("login.html",
                           initialized=bool(load_auth().get("password_hash")),
                           next_url=nxt, session_days=SESSION_DAYS)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/api/auth/status")
def api_auth_status():
    d = load_auth()
    logged = is_logged_in()
    return jsonify({
        "initialized": bool(d.get("password_hash")),
        "authenticated": logged,
        "protect_media": bool(d.get("protect_media", False)),
        "subscribe_token": d.get("subscribe_token", "") if logged else "",
        "session_days": SESSION_DAYS,
    })


@app.route("/api/auth/setup", methods=["POST"])
def api_auth_setup():
    if load_auth().get("password_hash"):
        return jsonify({"success": False, "message": "管理员密码已设置，请直接登录"}), 409
    data = request.get_json(silent=True) or {}
    pw = str(data.get("password") or "")
    if len(pw) < 8:
        return jsonify({"success": False, "message": "密码至少 8 位"}), 400
    if not hmac.compare_digest(pw, str(data.get("password2") or "")):
        return jsonify({"success": False, "message": "两次输入的密码不一致"}), 400
    create_auth(pw)
    session.clear()
    session.permanent = True
    session["hbtv_user"] = "admin"
    log("已设置管理员密码（保存在 config/auth.json，未入库）")
    return jsonify({"success": True, "message": "密码设置成功"})


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    d = load_auth()
    if not d.get("password_hash"):
        return jsonify({"success": False, "setup_required": True,
                        "message": "尚未设置密码，请先设置"}), 409
    ip = request.remote_addr or "?"
    wait = _login_blocked(ip)
    if wait:
        return jsonify({"success": False, "message": "尝试过于频繁，请 %d 秒后重试" % wait}), 429
    data = request.get_json(silent=True) or {}
    pw = str(data.get("password") or "")
    if not check_password_hash(d["password_hash"], pw):
        _login_failed(ip)
        return jsonify({"success": False, "message": "密码错误"}), 401
    _LOGIN_FAILS.pop(ip, None)
    session.clear()
    session.permanent = data.get("remember", True) is not False
    session["hbtv_user"] = "admin"
    log("管理员登录成功（来源 %s）" % ip)
    return jsonify({"success": True, "message": "登录成功"})


@app.route("/api/auth/password", methods=["POST"])
def api_auth_password():
    d = load_auth()
    if not d.get("password_hash"):
        return jsonify({"success": False, "message": "尚未设置密码"}), 401
    data = request.get_json(silent=True) or {}
    old = str(data.get("old_password") or "")
    if not check_password_hash(d["password_hash"], old):
        return jsonify({"success": False, "message": "原密码不正确"}), 401
    pw = str(data.get("new_password") or "")
    if len(pw) < 8:
        return jsonify({"success": False, "message": "新密码至少 8 位"}), 400
    if not hmac.compare_digest(pw, str(data.get("new_password2") or "")):
        return jsonify({"success": False, "message": "两次输入的新密码不一致"}), 400
    create_auth(pw)
    log("管理员密码已修改")
    return jsonify({"success": True, "message": "密码已修改"})


@app.route("/api/auth/media", methods=["POST"])
def api_auth_media():
    d = load_auth()
    if not d.get("password_hash"):
        return jsonify({"success": False, "message": "尚未设置密码"}), 401
    data = request.get_json(silent=True) or {}
    d["protect_media"] = bool(data.get("protect_media", False))
    if data.get("rotate_token") or not d.get("subscribe_token"):
        d["subscribe_token"] = secrets.token_urlsafe(24)
    save_auth(d)
    log("订阅令牌保护已%s" % ("开启" if d["protect_media"] else "关闭"))
    return jsonify({"success": True, "protect_media": d["protect_media"],
                    "subscribe_token": d["subscribe_token"]})


@app.route("/api/auth/token/rotate", methods=["POST"])
def api_auth_rotate():
    d = load_auth()
    if not d.get("password_hash"):
        return jsonify({"success": False, "message": "尚未设置密码"}), 401
    d["subscribe_token"] = secrets.token_urlsafe(24)
    save_auth(d)
    log("订阅令牌已重置，旧订阅地址立即失效")
    return jsonify({"success": True, "subscribe_token": d["subscribe_token"]})


@app.route("/")
def index():
    ch = parse_channels()
    return render_template("index.html", channels=ch[:60], channel_total=len(ch))


@app.route("/config")
def config():
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            print("读取配置失败:", e)
    return render_template("config.html", config=cfg)


@app.route("/save_config", methods=["POST"])
def save_config():
    try:
        data = request.get_json(force=True)
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        return jsonify({"success": True, "message": "配置已保存"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/output/<path:filename>")
def output_file(filename):
    """抓取产物：hbtv.m3u / hbtv.txt / epg.xml"""
    return send_from_directory(OUTPUT_DIR, filename)


@app.route("/logo/<path:filename>")
def logo_file(filename):
    """台标：本地文件，零外部请求。"""
    r = send_from_directory(LOGO_DIR, unquote(filename))
    r.headers["Cache-Control"] = "public, max-age=86400"
    return r


@app.route("/playlists")
@app.route("/playlists/")
def playlists_index():
    return redirect("/playlists/index.html")


@app.route("/playlists/<path:filename>")
def playlist_file(filename):
    """整理产物：支持 URL 参数动态改写，并把台标 / EPG 补成当前访问主机。"""
    filename = unquote(filename)
    if ".." in filename or filename.startswith("/"):
        return Response("not found", status=404)
    path = os.path.join(PLAYLIST_DIR, filename)
    if not os.path.isfile(path):
        return Response("not found: %s" % filename, status=404,
                        mimetype="text/plain; charset=utf-8")

    low = filename.lower()
    ext = os.path.splitext(low)[1]
    auth = load_auth()
    # 开启订阅令牌保护时，把令牌透进播放列表内的台标 / EPG 绝对地址，
    # 这样复制到播放器里的一个地址就足以拉到全部资源。
    suffix = ("?token=" + quote(auth["subscribe_token"])
              if auth.get("protect_media") and auth.get("subscribe_token") else "")
    if ext in (".m3u", ".m3u8", ".txt", ".xml", ".json"):
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        if ext in (".m3u", ".m3u8", ".txt"):
            q = parse_qs(request.query_string.decode("utf-8", "replace"),
                         keep_blank_values=True)
            if q:
                text = apply_params(text, q)
            text = absolutize(text, request.host_url, suffix)
        ctype = {
            ".m3u": "application/vnd.apple.mpegurl; charset=utf-8",
            ".m3u8": "application/vnd.apple.mpegurl; charset=utf-8",
            ".txt": "text/plain; charset=utf-8",
            ".xml": "application/xml; charset=utf-8",
            ".json": "application/json; charset=utf-8",
        }[ext]
        resp = Response(text, mimetype=ctype)
    elif ext == ".html":
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        tok = auth.get("subscribe_token", "") if auth.get("protect_media") else ""
        text = text.replace("</body>", "<script>window.HBTV_TOKEN=%s;</script></body>"
                            % json.dumps(tok))
        resp = Response(text, mimetype="text/html; charset=utf-8")
    else:
        resp = send_from_directory(PLAYLIST_DIR, filename)
        if ext == ".gz":
            resp.headers["Content-Type"] = "application/gzip"
    resp.headers["Cache-Control"] = "no-store"
    return resp


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
@app.route("/health")
def health():
    return jsonify({"ok": True, "time": now_str()})


@app.route("/api/files")
def api_files():
    items = []
    if os.path.isdir(PLAYLIST_DIR):
        for fn in sorted(os.listdir(PLAYLIST_DIR)):
            p = os.path.join(PLAYLIST_DIR, fn)
            if not os.path.isfile(p):
                continue
            ext = os.path.splitext(fn)[1].lower().lstrip(".")
            items.append({
                "name": fn,
                "kind": ext,
                "is_playlist": ext in ("m3u", "m3u8", "txt"),
                "size": os.path.getsize(p),
                "mtime": ts_str(os.path.getmtime(p)),
                "url": "/playlists/" + quote(fn),
            })
    manifest = {}
    mp = os.path.join(PLAYLIST_DIR, "manifest.json")
    if os.path.isfile(mp):
        try:
            with open(mp, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            pass
    return jsonify({"files": items, "manifest": manifest,
                    "count": len(items), "time": now_str()})


@app.route("/api/status")
def api_status():
    with JOB_LOCK:
        s = {k: STATE[k] for k in ("running", "mode", "trigger", "started_at",
                                   "finished_at", "ok", "error", "files",
                                   "last_result")}
        s["log_seq"] = len(STATE["logs"])
    with SCHED_LOCK:
        s["next_run"] = ts_str(_next_run[0])
    s["schedule"] = read_schedule()
    s["server_time"] = now_str()
    return jsonify(s)


@app.route("/api/logs")
def api_logs():
    try:
        since = int(request.args.get("since", 0))
    except ValueError:
        since = 0
    with JOB_LOCK:
        logs = STATE["logs"][since:]
        seq = len(STATE["logs"])
    return jsonify({"logs": logs, "seq": seq})


@app.route("/api/job/start", methods=["POST"])
def api_job_start():
    data = request.get_json(silent=True) or {}
    days = data.get("epg_days")
    if days is not None:
        try:
            days = int(days)
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "EPG 未来天数必须是数字"}), 400
        if not 1 <= days <= 7:
            return jsonify({"success": False, "message": "EPG 未来天数需在 1~7 之间"}), 400
    past = data.get("epg_past_days")
    if past is not None:
        try:
            past = int(past)
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "EPG 回看天数必须是数字"}), 400
        if not 0 <= past <= 14:
            return jsonify({"success": False, "message": "EPG 回看天数需在 0~14 之间"}), 400
    win = data.get("epg_window")
    ok, msg = start_job(data.get("mode", "organize"), trigger="manual",
                        epg_days=days, epg_past_days=past,
                        epg_window=(bool(win) if win is not None else None))
    return jsonify({"success": ok, "message": msg}), (200 if ok else 409)


@app.route("/api/schedule", methods=["GET", "POST"])
def api_schedule():
    if request.method == "GET":
        with SCHED_LOCK:
            nxt = ts_str(_next_run[0])
        return jsonify({"schedule": read_schedule(), "next_run": nxt})
    try:
        data = request.get_json(force=True) or {}
        cfg = read_organize_config()
        s = cfg.setdefault("schedule", {})
        for k in ("enabled", "run_fetch", "run_at_startup", "epg_window"):
            if k in data:
                s[k] = bool(data[k])
        if "mode" in data:
            if data["mode"] not in ("interval", "daily"):
                raise ValueError("mode 只能是 interval 或 daily")
            s["mode"] = data["mode"]
        if "interval_minutes" in data:
            v = int(data["interval_minutes"])
            if not 1 <= v <= 10080:
                raise ValueError("间隔需在 1~10080 分钟之间")
            s["interval_minutes"] = v
        if "daily_time" in data:
            v = str(data["daily_time"])
            m = re.match(r"^(\d{1,2}):(\d{2})$", v)
            if not m or not (0 <= int(m.group(1)) <= 23 and 0 <= int(m.group(2)) <= 59):
                raise ValueError("每日时间格式应为 HH:MM")
            s["daily_time"] = "%02d:%02d" % (int(m.group(1)), int(m.group(2)))
        if "epg_days" in data:
            v = int(data["epg_days"])
            if not 1 <= v <= 7:
                raise ValueError("EPG 未来天数需在 1~7 之间")
            s["epg_days"] = v
        if "epg_past_days" in data:
            v = int(data["epg_past_days"])
            if not 0 <= v <= 14:
                raise ValueError("EPG 回看天数需在 0~14 之间")
            s["epg_past_days"] = v
        with open(ORGANIZE_CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
        with SCHED_LOCK:
            _next_run[0] = None        # 让调度线程按新配置重算
        log("定时整理设置已更新：enabled=%s mode=%s" % (s.get("enabled"), s.get("mode")))
        return jsonify({"success": True, "schedule": s})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400


@app.route("/api/organize_config", methods=["GET", "POST"])
def api_organize_config():
    """常用整理参数（不含任何凭据）。"""
    if request.method == "GET":
        c = read_organize_config()
        return jsonify({
            "list_name": c.get("list_name", ""),
            "preset": c.get("preset", ""),
            "exclude_keywords": c.get("exclude_keywords", []),
            "include_keywords": c.get("include_keywords", []),
            "write_m3u_alias": c.get("write_m3u_alias", True),
            "udpxy": c.get("proxy", {}).get("udpxy", ""),
            "rtp2httpd": c.get("proxy", {}).get("rtp2httpd", ""),
        })
    try:
        d = request.get_json(force=True) or {}
        c = read_organize_config()
        for k in ("list_name", "preset", "write_m3u_alias"):
            if k in d:
                c[k] = d[k]
        for k in ("exclude_keywords", "include_keywords"):
            if k in d:
                v = d[k]
                c[k] = [x.strip() for x in v.split(",") if x.strip()] if isinstance(v, str) else list(v)
        for k in ("udpxy", "rtp2httpd"):
            if k in d:
                c.setdefault("proxy", {})[k] = str(d[k]).strip()
        with open(ORGANIZE_CONFIG, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=2)
            f.write("\n")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    start_background_services()
    print("=" * 62)
    print(" HBTV All-in-One 已启动（纯内网，零外部请求）")
    print("   控制台  : http://0.0.0.0:%d/" % port)
    print("   订阅中心: http://<内网IP>:%d/playlists/" % port)
    print("   APTV    : http://<内网IP>:%d/playlists/hbtv_aptv.m3u" % port)
    print("   台标    : http://<内网IP>:%d/logo/CCTV1.png" % port)
    print("=" * 62)
    app.run(host="0.0.0.0", port=port, threaded=True,
            debug=False, use_reloader=False)
