#!/usr/bin/env python3
"""Weather station — rtl_433 collector + SQLite + Flask dashboard + TCP push.

Run:
    ./start.sh           # one-command launcher
    python3 wx_server.py # direct start (collector + dashboard)
"""
import json
import math
import os
import select
import socket
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import flask
from flask import Flask, g, jsonify, request

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wxstat.db")
app = Flask(__name__)

RTL_CMD = [
    "rtl_433",
    "-Y", "classic",
    "-f", "868.3M",
    "-s", "250k",
    "-g", "20",
    "-F", "json",
]

TCP_PORT  = int(os.environ.get("TCP_PORT", 8081))
HTTP_PORT = int(os.environ.get("HTTP_PORT", 8085))

# Ollama — local LLM weather analysis (enabled by default)
OLLAMA_ENABLED = os.environ.get("OLLAMA_ENABLED", "true").lower() in ("1", "true", "yes")
OLLAMA_URL     = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL   = os.environ.get("OLLAMA_MODEL", "qwen2.5:0.5b")
OLLAMA_CACHE_S = int(os.environ.get("OLLAMA_CACHE_S", 300))


# ═══════════════════════════════════════════════════
#  Database
# ═══════════════════════════════════════════════════

def init_db(conn):
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT NOT NULL,
            model TEXT,
            station_id INTEGER,
            temperature_C REAL,
            humidity INTEGER,
            wind_dir_deg INTEGER,
            wind_avg_m_s REAL,
            wind_max_m_s REAL,
            rain_mm REAL,
            battery_ok INTEGER
        )
    """)
    conn.commit()


def to_ms(kmh):
    """Convert km/h to m/s, returning None for None/missing."""
    if kmh is None:
        return None
    return round(kmh / 3.6, 2)


def insert(conn, data):
    conn.execute(
        """INSERT INTO readings
           (time, model, station_id, temperature_C, humidity,
            wind_dir_deg, wind_avg_m_s, wind_max_m_s, rain_mm, battery_ok)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            data.get("time", datetime.now(timezone.utc).isoformat()),
            data.get("model"),
            data.get("id"),
            data.get("temperature_C"),
            data.get("humidity"),
            data.get("wind_dir_deg"),
            to_ms(data.get("wind_avg_km_h")),
            to_ms(data.get("wind_max_km_h")),
            data.get("rain_mm"),
            data.get("battery_ok"),
        ),
    )
    conn.commit()


# ═══════════════════════════════════════════════════
#  TCP push server (ESP32 direct connection)
# ═══════════════════════════════════════════════════

class TCPPushServer:
    """Listens on a TCP port and pushes JSON readings to connected clients.

    Each reading is sent as a newline-delimited JSON object.
    Clients just open a raw TCP socket and read lines."""

    def __init__(self, port):
        self.port = port
        self.clients = []
        self._lock = threading.Lock()
        self._running = True

        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("0.0.0.0", port))
        self._server.listen(5)
        self._server.setblocking(False)

        self._accept_thread = threading.Thread(
            target=self._accept_loop, daemon=True
        )
        self._accept_thread.start()
        print(f"[tcp-push] listening on port {port}")

    def _accept_loop(self):
        while self._running:
            try:
                readable, _, _ = select.select([self._server], [], [], 1.0)
                if readable:
                    sock, addr = self._server.accept()
                    sock.setblocking(False)
                    with self._lock:
                        self.clients.append(sock)
                    n = len(self.clients)
                    print(f"[tcp-push] client {addr[0]}:{addr[1]} ({n} connected)")
            except Exception:
                pass

    def broadcast(self, line):
        """Send a line to all clients, pruning disconnected ones."""
        with self._lock:
            alive = []
            for sock in self.clients:
                try:
                    sock.sendall(line)
                    alive.append(sock)
                except Exception:
                    try:
                        sock.close()
                    except Exception:
                        pass
            self.clients = alive

    def stop(self):
        self._running = False
        with self._lock:
            for sock in self.clients:
                try:
                    sock.close()
                except Exception:
                    pass
            self.clients = []
        try:
            self._server.close()
        except Exception:
            pass


# ═══════════════════════════════════════════════════
#  Weather condition analysis (Ollama + rule-based fallback)
# ═══════════════════════════════════════════════════

_condition_cache = {"ts": 0, "result": None}
_condition_lock = threading.Lock()

# Rain change-detection — rain_mm from the sensor is a cumulative counter,
# NOT a current rate.  Once it has ever rained the counter stays > 0 forever,
# so we must track whether rain is *actively* falling by watching for increases.
_rain_state = {"last_mm": None, "increased_at": 0, "lock": threading.Lock()}
_RECENT_RAIN_WINDOW = 1800  # 30 min — treat as rainy if rain increased this recently


def _rain_is_active(rain_mm):
    """Returns True if rain appears to be *actively* falling.

    rtl_433 reports rain_mm as a cumulative total (since sensor start or reset),
    so `rain_mm > 0` on its own is not enough — it could be from yesterday.
    We track the last-seen value and only consider rain active if the counter
    has increased within the recent window."""
    if rain_mm is None or rain_mm <= 0:
        return False

    with _rain_state["lock"]:
        now = time.time()
        changed = _rain_state["last_mm"] is not None and rain_mm > _rain_state["last_mm"]

        if changed:
            _rain_state["last_mm"] = rain_mm
            _rain_state["increased_at"] = now
            return True

        # First reading after startup — assume NOT raining (wait for increase)
        if _rain_state["last_mm"] is None:
            _rain_state["last_mm"] = rain_mm
            _rain_state["increased_at"] = 0
            return False

        # No change since last reading — check recency window
        if _rain_state["increased_at"] > 0 and (now - _rain_state["increased_at"]) < _RECENT_RAIN_WINDOW:
            return True

        # value hasn't changed and window expired — update tracking to current
        _rain_state["last_mm"] = rain_mm
        return False


def _simple_condition(data):
    """Rule-based weather condition — always available, runs instantly."""
    temp = data.get("temperature_C")
    hum = data.get("humidity")
    wind = data.get("wind_avg_km_h", 0)
    wind_gust = data.get("wind_max_km_h", 0)
    rain = data.get("rain_mm", 0)
    wind_dir = data.get("wind_dir_deg")

    # Rain takes priority (but only if actively raining — not stale cumulative total)
    if _rain_is_active(rain):
        if wind_gust is not None and wind_gust > 30:
            return {"condition": "Stormy", "icon": "⛈️",
                    "description": f"Heavy rain with strong gusts {wind_gust} km/h, {temp}°C."}
        if wind is not None and wind > 15:
            return {"condition": "Rainy & Windy", "icon": "🌧️",
                    "description": f"Rain at {temp}°C with brisk wind {wind} km/h."}
        if temp is not None and temp < 2:
            return {"condition": "Sleet", "icon": "🌨️",
                    "description": f"Near-freezing rain, {temp}°C."}
        return {"condition": "Rainy", "icon": "🌧️",
                "description": f"Rain, {temp}°C with {hum}% humidity."}

    # Wind
    if wind_gust is not None and wind_gust > 40:
        return {"condition": "Windy", "icon": "💨",
                "description": f"Strong wind gusting {wind_gust} km/h from {wind_dir}°, {temp}°C."}
    if wind_gust is not None and wind_gust > 25:
        return {"condition": "Windy", "icon": "🌬️",
                "description": f"Fresh breeze {wind_gust} km/h, {temp}°C."}
    if wind is not None and wind > 12:
        return {"condition": "Breezy", "icon": "🍃",
                "description": f"Light breeze {wind} km/h, {temp}°C."}

    # Fog / overcast / clear by humidity
    if hum is not None and hum >= 95:
        if temp is not None and temp <= 10:
            return {"condition": "Foggy", "icon": "🌫️",
                    "description": f"High humidity with cool air, fog likely, {temp}°C."}
        return {"condition": "Overcast", "icon": "☁️",
                "description": f"Very humid {hum}%, overcast, {temp}°C."}
    if hum is not None and hum > 80:
        return {"condition": "Overcast", "icon": "☁️",
                "description": f"Mostly cloudy, {hum}% humidity, {temp}°C."}

    # Clear conditions
    if temp is not None and temp > 28:
        return {"condition": "Clear", "icon": "☀️",
                "description": f"Hot and sunny, {temp}°C."}
    if temp is not None and temp > 22:
        return {"condition": "Clear", "icon": "🌤️",
                "description": f"Warm and pleasant, {temp}°C."}
    if hum is not None and hum < 35:
        return {"condition": "Clear", "icon": "🌤️",
                "description": f"Dry and clear, {temp}°C."}

    # Default
    return {"condition": "Partly Cloudy", "icon": "⛅",
            "description": f"Mild conditions, {temp}°C with {hum}% humidity."}


def analyze_weather(data):
    """Call local Ollama for a natural-language description — condition & icon from rules."""
    global _condition_cache

    now = time.time()

    # Condition and icon always from deterministic rules (never cached)
    rule = _simple_condition(data)
    cond = rule["condition"]
    icon = rule["icon"]

    # Cache only the Ollama description text (TTL = OLLAMA_CACHE_S)
    desc_cache = None
    with _condition_lock:
        if _condition_cache["result"] and (now - _condition_cache["ts"]) < OLLAMA_CACHE_S:
            desc_cache = _condition_cache["result"].get("description")

    if not desc_cache:
        temp = data.get("temperature_C", "?")
        hum  = data.get("humidity", "?")
        wind = data.get("wind_avg_km_h", "?")
        gust = data.get("wind_max_km_h", "?")
        wdir = data.get("wind_dir_deg", "?")
        rain = data.get("rain_mm", "?")

        prompt = (
            f"Describe this weather in ONE short English sentence (max 15 words): "
            f"{temp}°C, {hum}% humidity, wind {wind} avg/{gust} max km/h from {wdir}°, "
            f"rain {rain} mm. Condition is: {cond}. Just the sentence, nothing else."
        )

        try:
            body = json.dumps({
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
            }).encode()
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/generate",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read())
            desc_cache = result.get("response", "").strip()

            # Clean up: remove quotes, take first sentence only
            desc_cache = desc_cache.strip('"').strip("'")
            if "." in desc_cache:
                desc_cache = desc_cache.split(".")[0].strip() + "."
            if len(desc_cache) > 120:
                desc_cache = desc_cache[:117] + "..."

            print(f"[ollama] {cond} — {desc_cache}")

        except Exception as e:
            print(f"[ollama] failed ({e}) — using rule-based description")
            desc_cache = rule["description"]
            if len(desc_cache) > 120:
                desc_cache = desc_cache[:117] + "..."

        # Cache the description for future reuse
        with _condition_lock:
            _condition_cache = {
                "ts": now,
                "result": {"condition": cond, "icon": icon, "description": desc_cache},
            }

    return {"condition": cond, "icon": icon, "description": desc_cache}


def _warmup_ollama():
    """Fire a tiny request to Ollama so the model is loaded before the first reading."""
    if not OLLAMA_ENABLED:
        return
    print(f"[ollama] warming up model '{OLLAMA_MODEL}' (first inference can take a while)...")
    try:
        body = json.dumps({
            "model": OLLAMA_MODEL,
            "prompt": "Say 'ok'",
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            result = json.loads(resp.read())
        raw = result.get("response", "").strip()
        print(f"[ollama] warmup done — model replied: {raw[:60]}")
    except Exception as e:
        print(f"[ollama] warmup failed ({e}) — will retry on first reading")


# ═══════════════════════════════════════════════════
#  rtl_433 collector (daemon thread)
# ═══════════════════════════════════════════════════

def _drain_stderr(pipe, prefix, lines_out):
    """Read stderr lines into a list for later printing (daemon thread)."""
    for line in pipe:
        line = line.strip()
        if line:
            lines_out.append(f"[{prefix}] {line}")


def _run_rtl433(conn, tcp):
    """Run rtl_433 subprocess, feeding parsed readings into the database."""
    proc = subprocess.Popen(
        RTL_CMD, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    stderr_lines = []
    stderr_thread = threading.Thread(
        target=_drain_stderr,
        args=(proc.stderr, "rtl_433", stderr_lines),
        daemon=True,
    )
    stderr_thread.start()

    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("model") != "Fineoffset-WHx080":
                continue

            insert(conn, msg)

            # Analyze conditions and attach to the message
            try:
                analysis = analyze_weather(msg) if OLLAMA_ENABLED else _simple_condition(msg)
                msg["condition"]   = analysis.get("condition")
                msg["icon"]        = analysis.get("icon")
                msg["description"] = analysis.get("description")
            except Exception:
                pass  # never block readings on analysis failure

            # Compute today's rain delta so clients see useful numbers
            # (raw rain_mm is cumulative since sensor boot, useless for display)
            try:
                tr = conn.execute(
                    "SELECT MAX(rain_mm) - MIN(rain_mm) FROM readings WHERE time >= date('now')"
                ).fetchone()
                if tr and tr[0] is not None:
                    msg["rain_mm"] = round(max(0, tr[0]), 1)  # overwrite with today's delta
                else:
                    msg["rain_mm"] = 0.0
            except Exception:
                pass

            temp = msg.get("temperature_C", "?")
            hum = msg.get("humidity", "?")
            ts = msg.get("time", "?")
            cond = msg.get("condition", "?")
            print(f"[collector] {ts}  temp={temp}°C  hum={hum}%  [{cond}]")

            if tcp:
                tcp.broadcast((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
    except Exception as e:
        print(f"[collector] Error: {e}")
    finally:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        stderr_thread.join(timeout=2)
        for l in stderr_lines:
            print(l, flush=True)


def _rtl433_loop():
    """Daemon thread: keep rtl_433 running, restarting on failure."""
    conn = sqlite3.connect(DB)
    init_db(conn)
    print(f"[collector] DB ready: {DB}")

    tcp = None
    if TCP_PORT:
        try:
            tcp = TCPPushServer(TCP_PORT)
        except Exception as e:
            print(f"[tcp-push] failed to start ({e}) — continuing without")
    else:
        print("[tcp-push] disabled (set TCP_PORT to enable)")

    # Warm up Ollama model before first reading (so it's loaded in GPU)
    _warmup_ollama()

    try:
        while True:
            print("[collector] Starting rtl_433...")
            _run_rtl433(conn, tcp)
            print("[collector] rtl_433 exited, restarting in 10s...")
            time.sleep(10)
    finally:
        if tcp:
            tcp.stop()
        conn.close()


# ═══════════════════════════════════════════════════
#  Flask web routes
# ═══════════════════════════════════════════════════

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def _close_db(_):
    db = g.pop("db", None)
    if db:
        db.close()


def dew_point(t, h):
    """Magnus formula for dew point."""
    if t is None or h is None:
        return None
    a, b = 17.27, 237.7
    gamma = (a * t) / (b + t) + math.log(h / 100.0)
    return round((b * gamma) / (a - gamma), 1)


def _lan_ip():
    """Detect the active LAN IP by attempting a UDP connect."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


@app.route("/api/current")
def api_current():
    db = get_db()
    row = db.execute(
        "SELECT * FROM readings ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return jsonify({"error": "no data"}), 404
    d = dict(row)
    d["dew_point_C"] = dew_point(d.get("temperature_C"), d.get("humidity"))

    # Compute rain today from the cumulative counter
    # (rain_mm is cumulative since sensor boot — subtract day-start min)
    today_start = db.execute(
        "SELECT MIN(rain_mm) as rain_start, MAX(rain_mm) as rain_end "
        "FROM readings WHERE time >= date('now')"
    ).fetchone()
    d["rain_total_mm"] = d.get("rain_mm")  # raw cumulative
    if today_start and today_start["rain_start"] is not None:
        d["rain_today_mm"] = round(max(0, today_start["rain_end"] - today_start["rain_start"]), 1)
    else:
        d["rain_today_mm"] = 0.0

    return jsonify(d)


@app.route("/api/history")
def api_history():
    limit = request.args.get("limit", 288, type=int)
    since = request.args.get("since", type=int)
    db = get_db()
    if since is not None:
        rows = db.execute(
            "SELECT * FROM readings WHERE id > ? ORDER BY id DESC LIMIT ?",
            (since, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM readings ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return jsonify([dict(r) for r in reversed(rows)])


@app.route("/api/stats")
def api_stats():
    """Today's min/max for each field, plus rain today."""
    db = get_db()
    row = db.execute("""
        SELECT MIN(temperature_C) as t_min, MAX(temperature_C) as t_max,
               MIN(humidity) as h_min, MAX(humidity) as h_max,
               MAX(wind_max_m_s) as w_max,
               MIN(rain_mm) as rain_start, MAX(rain_mm) as rain_end
        FROM readings
        WHERE time >= date('now')  -- UTC, matches collector datetimes
    """).fetchone()
    if not row:
        return jsonify({})
    d = dict(row)
    if d["rain_start"] is not None and d["rain_end"] is not None:
        d["rain_today_mm"] = round(max(0, d["rain_end"] - d["rain_start"]), 1)
    else:
        d["rain_today_mm"] = 0.0
    return jsonify(d)


@app.route("/")
def dashboard():
    hostname = socket.gethostname()
    lan_ip = _lan_ip()
    web_url = f"{lan_ip}:{HTTP_PORT}"
    tcp_url = f"{lan_ip}:{TCP_PORT}" if TCP_PORT else "disabled"

    return _DASHBOARD_HTML.replace("__HOSTNAME__", hostname) \
                          .replace("__LAN_IP__", lan_ip) \
                          .replace("__WEB_URL__", web_url) \
                          .replace("__TCP_URL__", tcp_url)


# ═══════════════════════════════════════════════════
#  Dashboard template (cyberpunk theme)
# ═══════════════════════════════════════════════════

_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WX Station — 868.3 MHz</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;800&family=Share+Tech+Mono&display=swap');

  :root {
    --bg:       #0a0a0f;
    --surface:  #0d1117;
    --surface2: #161b22;
    --border:   #1a1a2e;
    --text:     #c0caf5;
    --muted:    #565f89;
    --cyan:     #00f0ff;
    --magenta:  #ff00aa;
    --green:    #00ff88;
    --yellow:   #ffcc00;
    --purple:   #bd93f9;
    --red:      #ff3344;
    --radius:   12px;
    --font:     'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;
    --font-ui:  'Share Tech Mono', 'JetBrains Mono', monospace;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: var(--font-ui);
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    padding: clamp(16px, 3vw, 32px);
    overflow-x: hidden;
    /* subtle grid */
    background-image:
      linear-gradient(rgba(0,240,255,0.03) 1px, transparent 1px),
      linear-gradient(90deg, rgba(0,240,255,0.03) 1px, transparent 1px);
    background-size: 40px 40px;
    /* radial vignette */
    background-attachment: fixed;
    position: relative;
  }
  body::before {
    content: '';
    position: fixed; inset: 0;
    background: radial-gradient(ellipse at 50% 0%, rgba(0,240,255,0.04) 0%, transparent 70%);
    pointer-events: none; z-index: 0;
  }

  /* ── Header ───────────────────────────── */
  .header {
    position: relative; z-index: 1;
    display: flex; align-items: flex-start; justify-content: space-between;
    margin-bottom: clamp(16px, 2vw, 24px);
    flex-wrap: wrap; gap: 12px;
  }
  .header-left h1 {
    font-family: var(--font);
    font-size: clamp(1.4em, 3vw, 2em); font-weight: 800;
    color: var(--cyan);
    text-shadow: 0 0 20px rgba(0,240,255,0.3), 0 0 60px rgba(0,240,255,0.1);
    letter-spacing: -0.02em;
  }
  .header-left .freq {
    font-family: var(--font); font-size: 0.75em; color: var(--muted);
    margin-top: 2px;
  }

  /* status bar */
  .status-bar {
    position: relative; z-index: 1;
    display: flex; align-items: center; gap: 8px;
    font-family: var(--font); font-size: 0.78em; color: var(--muted);
  }
  .status-dot {
    width: 10px; height: 10px; border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 8px var(--green), 0 0 16px rgba(0,255,136,0.4);
    display: inline-block;
  }
  .status-dot.stale { background: var(--red); box-shadow: 0 0 8px var(--red), 0 0 16px rgba(255,51,68,0.4); }
  .status-bar .sep { color: var(--border); }

  /* network info */
  .net-info {
    position: relative; z-index: 1;
    display: flex; flex-wrap: wrap; gap: clamp(8px, 1.5vw, 16px);
    margin-bottom: clamp(16px, 2vw, 24px);
    font-family: var(--font); font-size: clamp(0.65em, 1.1vw, 0.75em);
    color: var(--muted);
    padding: 10px 14px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
  }
  .net-info .label { color: var(--cyan); }
  .net-info .value { color: var(--text); font-weight: 600; }
  .net-info .sep { color: var(--border); margin: 0 4px; }

  /* ── Cards ────────────────────────────── */
  .cards {
    position: relative; z-index: 1;
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(clamp(180px, 20vw, 240px), 1fr));
    gap: clamp(10px, 1.5vw, 16px);
    margin-bottom: clamp(20px, 3vw, 28px);
  }
  .card {
    background: var(--surface);
    border-radius: var(--radius);
    padding: clamp(14px, 2vw, 20px) clamp(12px, 1.5vw, 16px);
    border: 1px solid var(--border);
    position: relative; overflow: hidden;
    transition: transform 0.2s, border-color 0.3s, box-shadow 0.3s;
  }
  .card:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 32px rgba(0,0,0,0.5);
  }
  /* neon top accent */
  .card::before {
    content: '';
    position: absolute; top: 0; left: 0; right: 0; height: 2px;
    border-radius: var(--radius) var(--radius) 0 0;
  }
  .card.temp::before { background: var(--magenta); box-shadow: 0 0 12px var(--magenta), 0 0 24px rgba(255,0,170,0.3); }
  .card.hum::before  { background: var(--cyan);   box-shadow: 0 0 12px var(--cyan),   0 0 24px rgba(0,240,255,0.3); }
  .card.wind::before { background: var(--green);  box-shadow: 0 0 12px var(--green),  0 0 24px rgba(0,255,136,0.3); }
  .card.dew::before  { background: var(--purple); box-shadow: 0 0 12px var(--purple), 0 0 24px rgba(189,147,249,0.3); }
  .card.rain::before { background: var(--yellow); box-shadow: 0 0 12px var(--yellow), 0 0 24px rgba(255,204,0,0.3); }
  .card .icon { font-size: clamp(1.2em, 2vw, 1.6em); margin-bottom: 4px; }
  .card .label {
    font-family: var(--font);
    font-size: clamp(0.6em, 0.9vw, 0.7em);
    color: var(--muted);
    text-transform: uppercase; letter-spacing: 0.1em;
    margin-bottom: 4px;
  }
  .card .main-val {
    font-family: var(--font);
    font-size: clamp(2em, 3.5vw, 2.8em); font-weight: 800;
    line-height: 1; margin: 4px 0;
  }
  .card .unit { font-size: 0.4em; font-weight: 400; opacity: 0.6; }
  .card .sub {
    font-family: var(--font);
    font-size: clamp(0.65em, 0.9vw, 0.75em); color: var(--muted);
    margin-top: 2px;
  }
  .card .range {
    font-family: var(--font);
    font-size: clamp(0.58em, 0.8vw, 0.65em); color: #3b4261;
    margin-top: 8px; padding-top: 8px;
    border-top: 1px solid var(--border);
  }

  /* card-specific text colors */
  .card.temp .main-val { color: var(--magenta); text-shadow: 0 0 18px rgba(255,0,170,0.25); }
  .card.hum  .main-val { color: var(--cyan);    text-shadow: 0 0 18px rgba(0,240,255,0.25); }
  .card.wind .main-val { color: var(--green);   text-shadow: 0 0 18px rgba(0,255,136,0.25); }
  .card.dew  .main-val { color: var(--purple);  text-shadow: 0 0 18px rgba(189,147,249,0.25); }
  .card.rain .main-val { color: var(--yellow);  text-shadow: 0 0 18px rgba(255,204,0,0.25); }

  /* ── Wind compass ──────────────────────── */
  .compass { width: clamp(60px, 10vw, 80px); height: clamp(60px, 10vw, 80px); flex-shrink: 0; }

  /* ── Badges ────────────────────────────── */
  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 10px;
    font-family: var(--font); font-size: 0.7em; font-weight: 600;
    border: 1px solid;
  }
  .badge.good { background: rgba(0,255,136,0.1);  color: var(--green);  border-color: rgba(0,255,136,0.3); }
  .badge.warn { background: rgba(255,204,0,0.1);  color: var(--yellow); border-color: rgba(255,204,0,0.3); }
  .badge.bad  { background: rgba(255,51,68,0.1);  color: var(--red);    border-color: rgba(255,51,68,0.3); }

  /* ── Rain bar ──────────────────────────── */
  .rain-bar {
    height: 5px; background: var(--surface2); border-radius: 3px;
    margin-top: 10px; overflow: hidden;
  }
  .rain-bar-fill {
    height: 100%; border-radius: 3px;
    background: linear-gradient(90deg, var(--yellow), var(--magenta));
    box-shadow: 0 0 8px rgba(255,204,0,0.3);
    transition: width 0.5s;
  }

  /* ── Charts ────────────────────────────── */
  .charts {
    position: relative; z-index: 1;
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(clamp(340px, 40vw, 500px), 1fr));
    gap: clamp(10px, 1.5vw, 16px);
  }
  .chart-box {
    background: var(--surface);
    border-radius: var(--radius);
    border: 1px solid var(--border);
    padding: clamp(12px, 1.5vw, 16px);
  }
  .chart-box h2 {
    font-family: var(--font);
    font-size: clamp(0.65em, 0.85vw, 0.72em);
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-bottom: 8px;
  }

  /* ── Scanline overlay ──────────────────── */
  .scanlines {
    position: fixed; inset: 0; pointer-events: none; z-index: 9999;
    background: repeating-linear-gradient(
      0deg,
      transparent,
      transparent 2px,
      rgba(0,0,0,0.03) 2px,
      rgba(0,0,0,0.03) 4px
    );
  }

  /* ── Chart pulse animation ────────────── */
  @keyframes chartPulse {
    0%   { box-shadow: 0 0 0 0 rgba(0,240,255,0.2); }
    50%  { box-shadow: 0 0 12px 4px rgba(0,240,255,0.15); }
    100% { box-shadow: 0 0 0 0 rgba(0,240,255,0); }
  }
  .chart-pulse {
    animation: chartPulse 0.8s ease-out;
    border-radius: var(--radius);
  }

  /* ── Responsive ────────────────────────── */
  @media (max-width: 700px) {
    .cards { grid-template-columns: repeat(2, 1fr); }
    .charts { grid-template-columns: 1fr; }
    .net-info { font-size: 0.6em; }
  }
  @media (min-width: 1600px) {
    body { padding: 36px 48px; }
    .cards { grid-template-columns: repeat(5, 1fr); }
    .charts { grid-template-columns: repeat(2, 1fr); }
  }
</style>
</head>
<body>
<div class="scanlines"></div>

<div class="header">
  <div class="header-left">
    <h1>// WX_STATION</h1>
    <div class="freq">868.300 MHz &nbsp;▸&nbsp; Fine Offset WHx080</div>
  </div>
  <div class="status-bar">
    <span id="status-dot" class="status-dot"></span>
    <span id="status-text">--</span>
    <span class="sep">│</span>
    <span>ID:<strong id="station-id">--</strong></span>
    <span class="sep">│</span>
    <span id="age">--</span>
  </div>
</div>

<div class="net-info">
  <span class="label">[HOST]</span> <span class="value">__HOSTNAME__</span>
  <span class="sep">│</span>
  <span class="label">[IP]</span> <span class="value">__LAN_IP__</span>
  <span class="sep">│</span>
  <span class="label">[WEB]</span> <span class="value">__WEB_URL__</span>
  <span class="sep">│</span>
  <span class="label">[TCP]</span> <span class="value">__TCP_URL__</span>
</div>

<div class="cards">
  <div class="card temp">
    <div class="icon" id="icon-temp"></div>
    <div class="label">Temperature</div>
    <div class="main-val" id="temp">--<span class="unit">°C</span></div>
    <div class="sub" id="feels"></div>
    <div class="range" id="range-temp"></div>
  </div>
  <div class="card hum">
    <div class="icon" id="icon-hum"></div>
    <div class="label">Humidity</div>
    <div class="main-val" id="hum">--<span class="unit">%</span></div>
    <div class="sub" id="comfort"></div>
    <div class="range" id="range-hum"></div>
  </div>
  <div class="card wind">
    <div class="icon" id="icon-wind"></div>
    <div class="label">Wind</div>
    <div style="display:flex;align-items:center;gap:10px;">
      <div class="compass" id="compass"></div>
      <div>
        <div style="font-family:var(--font);font-size:clamp(1.2em,1.8vw,1.4em);font-weight:800;" id="wind-dir">--</div>
        <div class="sub" id="wind-speed">--</div>
        <div class="sub" id="beaufort"></div>
      </div>
    </div>
    <div class="range" id="range-wind"></div>
  </div>
  <div class="card dew">
    <div class="icon" id="icon-dew"></div>
    <div class="label">Dew Point</div>
    <div class="main-val" id="dew">--<span class="unit">°C</span></div>
    <div class="sub" id="dew-note"></div>
  </div>
  <div class="card rain">
    <div class="icon" id="icon-rain"></div>
    <div class="label">Rain Today</div>
    <div class="main-val" style="font-size:clamp(1.8em,3vw,2.4em);" id="rain">--<span class="unit"> mm</span></div>
    <div class="rain-bar"><div class="rain-bar-fill" id="rain-bar" style="width:0%"></div></div>
    <div class="sub" style="margin-top:4px;" id="rain-total"></div>
  </div>
</div>

<div class="charts">
  <div class="chart-box"><h2>▸ Temperature · 24h</h2><canvas id="tempChart" height="100"></canvas></div>
  <div class="chart-box"><h2>▸ Humidity · 24h</h2><canvas id="humChart" height="100"></canvas></div>
  <div class="chart-box"><h2>▸ Wind Speed · 24h</h2><canvas id="windChart" height="100"></canvas></div>
  <div class="chart-box"><h2>▸ Rain · 24h</h2><canvas id="rainChart" height="100"></canvas></div>
</div>

<script>
// ── Helpers ──────────────────────────────────
const WL = ['N','NE','E','SE','S','SW','W','NW'];
const WA = ['↑','↗','→','↘','↓','↙','←','↖'];

function windDirStr(d) {
  if (d == null) return '--';
  const i = Math.round(d / 45) % 8;
  return WA[i] + ' ' + WL[i] + ' ' + d + '°';
}

function beaufort(ms) {
  if (ms == null) return '';
  if (ms < 0.5) return ['Calm','😌'];
  if (ms < 1.6) return ['Light air','🍃'];
  if (ms < 3.4) return ['Light breeze','🌿'];
  if (ms < 5.5) return ['Gentle breeze','🌬️'];
  if (ms < 8.0) return ['Moderate','💨'];
  if (ms < 10.8) return ['Fresh breeze','🌳'];
  if (ms < 13.9) return ['Strong','⚠️'];
  if (ms < 17.2) return ['High wind','🌪️'];
  if (ms < 20.8) return ['Gale','❗'];
  return ['Storm','🔥'];
}

function tempIcon(t) {
  if (t == null) return '🌡️';
  if (t <= -10) return '🥶'; if (t <= 0) return '❄️';
  if (t < 8) return '🌬️'; if (t < 16) return '🌤️';
  if (t < 24) return '☀️'; if (t < 30) return '🏖️';
  return '🥵';
}
function humIcon(h) {
  if (h == null) return '💧'; if (h < 30) return '🏜️';
  if (h < 60) return '🌿'; if (h < 85) return '💧';
  return '🌊';
}
function windIcon(s) {
  if (s == null || s === 0) return '🍃';
  if (s < 3) return '🌿'; if (s < 8) return '🌬️';
  return '💨';
}
function rainIcon(r) {
  if (r == null || r === 0) return '☀️';
  if (r < 1) return '🌂'; if (r < 5) return '🌧️';
  return '⛈️';
}
function dewIcon(dp) {
  if (dp == null) return '💧';
  if (dp < 0) return '🧊'; if (dp < 10) return '💧';
  if (dp < 20) return '🌫️'; return '🔥';
}

function comfort(h) {
  if (h == null) return '';
  if (h >= 40 && h <= 60) return '<span class="badge good">Ideal</span>';
  if (h >= 30 && h <= 70) return '<span class="badge warn">OK</span>';
  return '<span class="badge bad">'+ (h > 70 ? 'Humid' : 'Dry') +'</span>';
}

function drawCompass(deg) {
  const c = document.getElementById('compass');
  c.innerHTML = '<svg viewBox="0 0 100 100">' +
    '<circle cx="50" cy="50" r="46" fill="none" stroke="#1a1a2e" stroke-width="2"/>' +
    '<circle cx="50" cy="50" r="38" fill="none" stroke="#0d1117" stroke-width="1" stroke-dasharray="2,4"/>' +
    '<line x1="50" y1="8" x2="50" y2="16" stroke="#565f89" stroke-width="1.5"/>' +
    '<line x1="50" y1="84" x2="50" y2="92" stroke="#3b4261" stroke-width="1"/>' +
    '<line x1="8" y1="50" x2="16" y2="50" stroke="#3b4261" stroke-width="1"/>' +
    '<line x1="84" y1="50" x2="92" y2="50" stroke="#3b4261" stroke-width="1"/>' +
    '<text x="50" y="12" text-anchor="middle" fill="#00f0ff" font-size="8" font-family="monospace">N</text>' +
    '<polygon points="50,18 42,55 50,48 58,55" fill="#00ff88"' +
    ' filter="drop-shadow(0 0 3px #00ff88)"' +
    ' transform="rotate(' + (deg||0) + ',50,50)" style="transition:transform 0.6s"/>' +
    '</svg>';
}

// ── Charts (cyberpunk palette) ─────────────
const C = { magenta:'#ff00aa', cyan:'#00f0ff', green:'#00ff88', purple:'#bd93f9' };

function pulseChart(id) {
  var el = document.getElementById(id);
  if (!el) return;
  el.classList.remove('chart-pulse');
  void el.offsetWidth;
  el.classList.add('chart-pulse');
  setTimeout(function() { el.classList.remove('chart-pulse'); }, 800);
}

function makeGrad(ctx, h, fromColor) {
  var g = ctx.createLinearGradient(0, 0, 0, h);
  g.addColorStop(0, fromColor + '50');
  g.addColorStop(1, fromColor + '05');
  return g;
}

function drawChart(canvasId, rows, key, color, fromColor) {
  const ctx = document.getElementById(canvasId).getContext('2d');
  const labels = rows.map(r => r.time.slice(11,16));
  const data = rows.map(r => r[key]);
  const key2 = canvasId + '_chart';

  if (window[key2]) {
    var chart = window[key2];
    var ch = chart.chartArea ? chart.chartArea.bottom : 110;
    var grad = makeGrad(chart.ctx, ch, fromColor);
    chart.data.labels = labels;
    chart.data.datasets[0].data = data;
    chart.data.datasets[0].backgroundColor = grad;
    chart.update('default');
    pulseChart(canvasId);
    return;
  }

  window[key2] = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets: [{ data, borderColor: color, backgroundColor: makeGrad(ctx, 110, fromColor),
      borderWidth: 2, pointRadius: 0, tension: 0.35, fill: true }] },
    options: {
      responsive: true,
      animation: { duration: 600, easing: 'easeOutCubic',
        y: { from: function(a) { if (a.type === 'data' && a.index > 0) return a.dataset.data[a.index - 1]; } }
      },
      interaction: { intersect: false, mode: 'index' },
      scales: {
        x: { ticks: { color: '#3b4261', maxTicksLimit: 10, font: { size: 10, family: 'monospace' } },
             grid: { color: '#1a1a2e' } },
        y: { ticks: { color: '#3b4261', font: { size: 10, family: 'monospace' } },
             grid: { color: '#1a1a2e' } }
      },
      plugins: { legend: { display: false },
        tooltip: {
          mode: 'index', intersect: false,
          backgroundColor: '#0d1117',
          borderColor: color, borderWidth: 1,
          titleColor: '#c0caf5', bodyColor: '#c0caf5',
          titleFont: { family: 'monospace' }, bodyFont: { family: 'monospace' }
        }
      }
    }
  });
}

// ── Refresh loop ─────────────────────────────
var lastId = 0;
var failCount = 0;
var historyRows = [];

async function refresh() {
  var ok = false;
  try {
    var r = await fetch('/api/current');
    if (!r.ok) { failCount++; }
    else {
      ok = true;
      var d = await r.json();
      var t = d.temperature_C;
      var h = d.humidity;
      var ws = d.wind_avg_m_s;
      var wg = d.wind_max_m_s;
      var wd = d.wind_dir_deg;
      var rain = d.rain_today_mm;
      var dp = d.dew_point_C;

      document.getElementById('icon-temp').textContent = tempIcon(t);
      document.getElementById('temp').innerHTML = (t != null ? t : '--') + '<span class="unit">°C</span>';
      var fl = '';
      if (t != null) {
        if (t < 0) fl = 'Freezing'; else if (t < 10) fl = 'Chilly'; else if (t < 20) fl = 'Mild'; else if (t < 28) fl = 'Warm'; else fl = 'Hot';
      }
      document.getElementById('feels').textContent = fl;

      document.getElementById('icon-hum').textContent = humIcon(h);
      document.getElementById('hum').innerHTML = (h != null ? h : '--') + '<span class="unit">%</span>';
      document.getElementById('comfort').innerHTML = comfort(h);

      document.getElementById('icon-wind').textContent = windIcon(wg);
      document.getElementById('wind-dir').textContent = windDirStr(wd);
      document.getElementById('wind-speed').textContent = (ws != null ? ws.toFixed(1) : '--') + ' avg / ' + (wg != null ? wg.toFixed(1) : '--') + ' gust m/s';
      var bf = beaufort(ws);
      document.getElementById('beaufort').innerHTML = bf ? bf[1] + ' ' + bf[0] : '';

      document.getElementById('icon-dew').textContent = dewIcon(dp);
      document.getElementById('dew').innerHTML = (dp != null ? dp : '--') + '<span class="unit">°C</span>';
      var dn = '';
      if (dp != null && t != null) {
        var spread = t - dp;
        if (spread < 2) dn = 'Fog likely';
        else if (spread < 5) dn = 'Comfortable';
        else dn = 'Dry air';
      }
      document.getElementById('dew-note').textContent = dn;

      document.getElementById('icon-rain').textContent = rainIcon(rain);
      document.getElementById('rain').innerHTML = (rain != null ? rain.toFixed(1) : '--') + '<span class="unit"> mm</span>';
      document.getElementById('rain-bar').style.width = Math.min(100, ((rain||0) / 20) * 100) + '%';
      drawCompass(wd);
      document.getElementById('station-id').textContent = d.station_id || '--';

      var age = (Date.now() / 1000) - (new Date(d.time + 'Z').getTime() / 1000);
      var dot = document.getElementById('status-dot');
      document.getElementById('age').textContent = age < 120 ? 'live' : Math.round(age / 60) + 'm ago';
      dot.className = age > 300 ? 'status-dot stale' : 'status-dot';
      document.getElementById('status-text').textContent = age > 300 ? 'Stale' : 'Live';

      try {
        var sr = await fetch('/api/stats');
        var s = await sr.json();
        document.getElementById('range-temp').textContent = s.t_min != null ? '↓ ' + s.t_min + '°  ↑ ' + s.t_max + '°' : '';
        document.getElementById('range-hum').textContent = s.h_min != null ? '↓ ' + s.h_min + '%  ↑ ' + s.h_max + '%' : '';
        document.getElementById('range-wind').textContent = s.w_max != null ? 'Gust max ' + s.w_max.toFixed(1) + ' m/s' : '';
        document.getElementById('rain-total').textContent =
          (d.rain_total_mm != null ? 'Total: ' + d.rain_total_mm.toFixed(1) + ' mm' : '') +
          (s.rain_today_mm != null ? ' · Today: ' + s.rain_today_mm.toFixed(1) + ' mm' : '');
      } catch(e) {}
    }
  } catch(e) { failCount++; }

  if (ok) { failCount = 0; }
  else if (failCount >= 2) {
    document.getElementById('status-dot').className = 'status-dot stale';
    document.getElementById('status-text').textContent = 'Offline';
    document.getElementById('age').textContent = '—';
  }

  try {
    var url = lastId > 0 ? '/api/history?limit=288&since=' + lastId : '/api/history?limit=288';
    var hr = await fetch(url);
    var rows = await hr.json();
    if (rows.length === 0) return;

    for (var i = 0; i < rows.length; i++) {
      if (rows[i].id > lastId) lastId = rows[i].id;
    }

    if (lastId > 0 && rows.length > 0 && historyRows.length > 0) {
      historyRows = historyRows.concat(rows);
      if (historyRows.length > 288) historyRows = historyRows.slice(historyRows.length - 288);
    } else {
      historyRows = rows;
    }

    drawChart('tempChart', historyRows, 'temperature_C', C.magenta, C.magenta);
    drawChart('humChart',  historyRows, 'humidity',       C.cyan,    C.cyan);
    drawChart('windChart', historyRows, 'wind_avg_m_s',    C.green,   C.green);
    drawChart('rainChart', historyRows, 'rain_mm',         C.purple,  C.purple);
  } catch(e) {}
}

refresh();
setInterval(refresh, 60000);
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════
#  Startup
# ═══════════════════════════════════════════════════

if __name__ == "__main__":
    # Init DB schema + WAL before starting anything
    conn = sqlite3.connect(DB)
    init_db(conn)
    conn.close()

    # Start collector in background daemon thread
    threading.Thread(target=_rtl433_loop, daemon=True).start()

    ip = _lan_ip()
    print(f"[server] Dashboard  → http://{ip}:{HTTP_PORT}")
    print(f"[server] TCP push   → {ip}:{TCP_PORT}" if TCP_PORT else "[server] TCP push   → disabled")

    app.run(host="0.0.0.0", port=HTTP_PORT, debug=False)
