#!/usr/bin/env python3
"""Weather station — rtl_433 collector + SQLite + Flask dashboard + TCP push.

Run:
    ./start.sh           # one-command launcher
    python3 wx_server.py # direct start (collector + dashboard)
"""
import json
import os
import select
import socket
import sqlite3
import subprocess
import threading
import time
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

TCP_PORT = int(os.environ.get("TCP_PORT", 8081))


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
            temp = msg.get("temperature_C", "?")
            hum = msg.get("humidity", "?")
            ts = msg.get("time", "?")
            print(f"[collector] {ts}  temp={temp}°C  hum={hum}%")

            if tcp:
                tcp.broadcast((json.dumps(msg) + "\n").encode())
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
    gamma = (a * t) / (b + t) + __import__("math").log(h / 100.0)
    return round((b * gamma) / (a - gamma), 1)


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
    """Today's min/max for each field."""
    db = get_db()
    rows = db.execute("""
        SELECT MIN(temperature_C) as t_min, MAX(temperature_C) as t_max,
               MIN(humidity) as h_min, MAX(humidity) as h_max,
               MAX(wind_max_m_s) as w_max
        FROM readings
        WHERE time >= date('now')  -- UTC, matches collector datetimes
    """).fetchone()
    return jsonify(dict(rows)) if rows else jsonify({})


@app.route("/")
def dashboard():
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WX Station — 868.3 MHz</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root {
    --bg: #0f172a; --surface: #1e293b; --surface2: #334155;
    --text: #e2e8f0; --muted: #94a3b8; --accent: #38bdf8;
    --temp: #fb923c; --hum: #38bdf8; --wind: #4ade80;
    --rain: #a78bfa; --dew: #c084fc; --low: #f87171; --ok: #4ade80;
    --radius: 16px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Inter', system-ui, -apple-system, sans-serif;
    background: var(--bg); color: var(--text);
    min-height: 100vh; padding: 24px;
  }
  .header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; flex-wrap: wrap; gap: 12px; }
  .header h1 { font-size: 1.6em; font-weight: 700; background: linear-gradient(135deg, var(--accent), #818cf8); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
  .header .meta { display: flex; gap: 16px; align-items: center; font-size: 0.85em; color: var(--muted); }
  .status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ok); display: inline-block; }
  .status-dot.stale { background: var(--low); }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 14px; margin-bottom: 28px; }
  .card {
    background: var(--surface); border-radius: var(--radius); padding: 18px 16px;
    position: relative; overflow: hidden; border: 1px solid rgba(255,255,255,0.05);
    transition: transform 0.2s, box-shadow 0.2s;
  }
  .card:hover { transform: translateY(-2px); box-shadow: 0 8px 25px rgba(0,0,0,0.3); }
  .card .icon { font-size: 1.6em; margin-bottom: 6px; }
  .card .label { font-size: 0.75em; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; }
  .card .value { font-size: 2em; font-weight: 800; margin: 4px 0; }
  .card .sub { font-size: 0.8em; color: var(--muted); }
  .card .range { font-size: 0.7em; color: #64748b; margin-top: 6px; }
  .card::after { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px; border-radius: var(--radius) var(--radius) 0 0; }
  .card.temp { --ac: var(--temp); }
  .card.hum  { --ac: var(--hum); }
  .card.wind { --ac: var(--wind); }
  .card.rain { --ac: var(--rain); }
  .card.dew  { --ac: var(--dew); }
  .card.temp::after, .card.hum::after, .card.wind::after, .card.rain::after, .card.dew::after { background: var(--ac); }
  .card .main-val { font-size: 2.4em; font-weight: 800; line-height: 1; }
  .card .unit { font-size: 0.45em; font-weight: 500; }
  .compass { width: 70px; height: 70px; margin: 4px auto; }
  .compass svg { width: 100%; height: 100%; }
  .rain-bar { height: 6px; background: var(--surface2); border-radius: 3px; margin-top: 8px; overflow: hidden; }
  .rain-bar-fill { height: 100%; background: linear-gradient(90deg, var(--rain), #c084fc); border-radius: 3px; transition: width 0.5s; }
  .badge { display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 0.75em; font-weight: 600; }
  .badge.good { background: #065f46; color: #6ee7b7; }
  .badge.warn { background: #713f12; color: #fcd34d; }
  .badge.bad  { background: #7f1d1d; color: #fca5a5; }
  .beaufort { display: inline-block; margin-left: 4px; font-weight: 600; }
  .charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 16px; }
  .chart-box { background: var(--surface); border-radius: var(--radius); padding: 18px; border: 1px solid rgba(255,255,255,0.05); }
  .chart-box h2 { font-size: 0.8em; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 10px; font-weight: 600; }
  @media (max-width: 600px) {
    body { padding: 12px; }
    .cards { grid-template-columns: repeat(2, 1fr); }
    .charts { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>

<div class="header">
  <h1>Weather Station</h1>
  <div class="meta">
    <span id="status-dot" class="status-dot"></span>
    <span id="status-text">--</span>
    <span>ID <strong id="station-id">--</strong></span>
    <span id="age"></span>
  </div>
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
        <div style="font-size:1.6em;font-weight:800;" id="wind-dir">--</div>
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
    <div class="label">Rain Total</div>
    <div class="main-val" style="font-size:2.2em;" id="rain">--<span class="unit"> mm</span></div>
    <div class="rain-bar"><div class="rain-bar-fill" id="rain-bar" style="width:0%"></div></div>
  </div>
</div>

<div class="charts">
  <div class="chart-box"><h2>Temperature · 24h</h2><canvas id="tempChart" height="100"></canvas></div>
  <div class="chart-box"><h2>Humidity · 24h</h2><canvas id="humChart" height="100"></canvas></div>
  <div class="chart-box"><h2>Wind Speed · 24h</h2><canvas id="windChart" height="100"></canvas></div>
  <div class="chart-box"><h2>Rain Rate · 24h</h2><canvas id="rainChart" height="100"></canvas></div>
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
    '<circle cx="50" cy="50" r="46" fill="none" stroke="#334155" stroke-width="2"/>' +
    '<circle cx="50" cy="50" r="38" fill="none" stroke="#1e293b" stroke-width="1" stroke-dasharray="2,4"/>' +
    '<line x1="50" y1="8" x2="50" y2="15" stroke="#64748b" stroke-width="1.5"/>' +
    '<line x1="50" y1="85" x2="50" y2="92" stroke="#475569" stroke-width="1"/>' +
    '<line x1="8" y1="50" x2="15" y2="50" stroke="#475569" stroke-width="1"/>' +
    '<line x1="85" y1="50" x2="92" y2="50" stroke="#475569" stroke-width="1"/>' +
    '<text x="50" y="12" text-anchor="middle" fill="#94a3b8" font-size="8">N</text>' +
    '<polygon points="50,18 42,55 50,48 58,55" fill="#4ade80"' +
    ' transform="rotate(' + (deg||0) + ',50,50)" style="transition:transform 0.6s"/>' +
    '</svg>';
}

// ── Charts ───────────────────────────────────
function drawChart(canvasId, rows, key, label, color, fromColor) {
  const ctx = document.getElementById(canvasId).getContext('2d');
  const labels = rows.map(r => r.time.slice(11,16));
  const data = rows.map(r => r[key]);
  const key2 = canvasId + '_chart';
  if (window[key2]) window[key2].destroy();
  var grad = ctx.createLinearGradient(0, 0, 0, 110);
  grad.addColorStop(0, fromColor + '60');
  grad.addColorStop(1, fromColor + '05');
  window[key2] = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets: [{ label, data, borderColor: color, backgroundColor: grad, borderWidth: 2, pointRadius: 0, tension: 0.35, fill: true }] },
    options: {
      responsive: true, animation: { duration: 300 },
      interaction: { intersect: false, mode: 'index' },
      scales: {
        x: { ticks: { color: '#64748b', maxTicksLimit: 10, font: { size: 10 } }, grid: { color: '#1e293b' } },
        y: { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: '#1e293b' } }
      },
      plugins: { legend: { display: false }, tooltip: { mode: 'index', intersect: false } }
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
      var rain = d.rain_mm;
      var dp = d.dew_point_C;

      // Cards
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
      document.getElementById('beaufort').innerHTML = bf ? '<span class="beaufort">' + bf[1] + ' ' + bf[0] + '</span>' : '';

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

      // Status
      var age = (Date.now() / 1000) - (new Date(d.time + 'Z').getTime() / 1000);
      var dot = document.getElementById('status-dot');
      document.getElementById('age').textContent = age < 120 ? 'live' : Math.round(age / 60) + 'm ago';
      dot.className = age > 300 ? 'status-dot stale' : 'status-dot';
      document.getElementById('status-text').textContent = age > 300 ? 'Stale' : 'Live';

      // Today's min/max
      try {
        var sr = await fetch('/api/stats');
        var s = await sr.json();
        document.getElementById('range-temp').textContent = s.t_min != null ? '↓ ' + s.t_min + '°  ↑ ' + s.t_max + '°' : '';
        document.getElementById('range-hum').textContent = s.h_min != null ? '↓ ' + s.h_min + '%  ↑ ' + s.h_max + '%' : '';
        document.getElementById('range-wind').textContent = s.w_max != null ? 'Gust max ' + s.w_max.toFixed(1) + ' m/s' : '';
      } catch(e) {}
    }
  } catch(e) { failCount++; }

  // ── Connection status ──────────────────────
  if (ok) {
    failCount = 0;
  } else if (failCount >= 2) {
    document.getElementById('status-dot').className = 'status-dot stale';
    document.getElementById('status-text').textContent = 'Offline';
    document.getElementById('age').textContent = '—';
  }

  // ── History (incremental) ──────────────────
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

    drawChart('tempChart', historyRows, 'temperature_C', '°C', '#fb923c', '#fb923c');
    drawChart('humChart', historyRows, 'humidity', '%', '#38bdf8', '#38bdf8');
    drawChart('windChart', historyRows, 'wind_avg_m_s', 'm/s', '#4ade80', '#4ade80');
    drawChart('rainChart', historyRows, 'rain_mm', 'mm', '#a78bfa', '#a78bfa');
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

    app.run(host="127.0.0.1", port=8080, debug=False)
