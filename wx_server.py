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
from enum import Enum
from typing import Any, Dict, List, Optional

import flask
from flask import Flask, g, jsonify, request

_START_TIME = time.time()
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wxstat.db")
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wx_config.json")
app = Flask(__name__)

def _load_frequency():
    freq = os.environ.get("WX_FREQ")
    if freq:
        return freq
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                return data.get("frequency", "868.3M")
        except Exception:
            pass
    return "868.3M"

WX_FREQ = _load_frequency()
RTL_CMD = [
    "rtl_433",
    "-Y", "classic",
    "-f", WX_FREQ,
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
#  Explicit State Machines (UX / DX / AX Architecture)
# ═══════════════════════════════════════════════════

class BatteryState(str, Enum):
    """Hardware battery states reported by Fine Offset WHx080 sensor."""
    OK = "OK"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class SensorSignalState(str, Enum):
    """Reception freshness states of the sensor wireless telemetry."""
    LIVE = "LIVE"        # < 120s
    RECENT = "RECENT"    # 120s - 300s
    STALE = "STALE"      # > 300s
    UNKNOWN = "UNKNOWN"  # no data


class WeatherCategory(str, Enum):
    """Categorical classification of weather conditions."""
    STORMY = "Stormy"
    RAINY_WINDY = "Rainy & Windy"
    SLEET = "Sleet"
    RAINY = "Rainy"
    WINDY = "Windy"
    BREEZY = "Breezy"
    FOGGY = "Foggy"
    OVERCAST = "Overcast"
    CLEAR = "Clear"
    PARTLY_CLOUDY = "Partly Cloudy"


class CollectorState(str, Enum):
    """Lifecycle states of the rtl_433 SDR collector subprocess."""
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RECEIVING = "RECEIVING"
    RETRY_WAITING = "RETRY_WAITING"
    ERROR = "ERROR"


class CollectorStateMachine:
    """Thread-safe state machine governing the rtl_433 SDR decoder subprocess."""

    def __init__(self, frequency: str):
        self._lock = threading.RLock()
        self.state: CollectorState = CollectorState.STOPPED
        self.frequency: str = frequency
        self.pid: Optional[int] = None
        self.started_at: Optional[float] = None
        self.last_packet_time: Optional[float] = None
        self.packets_received: int = 0
        self.restart_count: int = 0
        self.last_error: Optional[str] = None

    def on_starting(self, pid: Optional[int] = None, frequency: Optional[str] = None) -> None:
        with self._lock:
            self.state = CollectorState.STARTING
            self.pid = pid
            if frequency:
                self.frequency = frequency
            self.started_at = time.time()
            self.last_error = None

    def on_packet_received(self) -> None:
        with self._lock:
            self.state = CollectorState.RECEIVING
            self.packets_received += 1
            self.last_packet_time = time.time()

    def on_retry_wait(self, error: Optional[str] = None) -> None:
        with self._lock:
            self.state = CollectorState.RETRY_WAITING
            self.pid = None
            self.restart_count += 1
            if error:
                self.last_error = error

    def on_stopped(self) -> None:
        with self._lock:
            self.state = CollectorState.STOPPED
            self.pid = None

    def on_error(self, error: str) -> None:
        with self._lock:
            self.state = CollectorState.ERROR
            self.last_error = error

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            packet_age_s = round(now - self.last_packet_time, 1) if self.last_packet_time else None
            uptime_s = (
                round(now - self.started_at, 1)
                if self.started_at and self.state in (CollectorState.STARTING, CollectorState.RECEIVING)
                else 0.0
            )
            return {
                "state": self.state.value,
                "pid": self.pid,
                "frequency": self.frequency,
                "uptime_s": uptime_s,
                "packets_received": self.packets_received,
                "packet_age_s": packet_age_s,
                "restart_count": self.restart_count,
                "last_error": self.last_error,
            }


class RainActivityState(str, Enum):
    """Precipitation activity states detected from sensor cumulative gauge."""
    NO_DATA = "NO_DATA"
    DRY = "DRY"
    ACTIVE_RAIN = "ACTIVE_RAIN"
    RECENT_RAIN = "RECENT_RAIN"


class RainStateMachine:
    """Thread-safe state machine for sensor precipitation tracking.

    rtl_433 reports rain_mm as a cumulative total since sensor reset.
    This machine handles:
    - Initial baseline acquisition
    - Transition to ACTIVE_RAIN on counter advance
    - Transition to RECENT_RAIN during the 30-min window after rain stops
    - Transition to DRY after the window elapses
    - Graceful recovery when sensor battery is changed / counter resets
    """

    def __init__(self, window_seconds: int = 1800):
        self._lock = threading.RLock()
        self.window_seconds: int = window_seconds
        self.state: RainActivityState = RainActivityState.NO_DATA
        self.last_mm: Optional[float] = None
        self.increased_at: float = 0.0

    def update(self, rain_mm: Optional[float], now: Optional[float] = None) -> "RainStateMachine":
        if now is None:
            now = time.time()

        if rain_mm is None or rain_mm < 0:
            return self

        with self._lock:
            # First reading initialization
            if self.last_mm is None:
                self.last_mm = rain_mm
                self.state = RainActivityState.DRY
                return self

            # Counter reset detection (e.g. battery swapped or sensor reconnected)
            if rain_mm < self.last_mm - 5.0:
                print(f"[rain-fsm] Sensor counter reset detected ({self.last_mm}mm -> {rain_mm}mm)")
                self.last_mm = rain_mm
                self.state = RainActivityState.DRY
                return self

            # Gauge increased: actively raining right now
            if rain_mm > self.last_mm:
                self.last_mm = rain_mm
                self.increased_at = now
                self.state = RainActivityState.ACTIVE_RAIN
                return self

            # Counter did not advance on this tick
            self.last_mm = rain_mm
            if self.increased_at > 0 and (now - self.increased_at) < self.window_seconds:
                self.state = RainActivityState.RECENT_RAIN
            else:
                self.state = RainActivityState.DRY

            return self

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self.state in (RainActivityState.ACTIVE_RAIN, RainActivityState.RECENT_RAIN)

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            sec_since_increase = round(now - self.increased_at, 1) if self.increased_at > 0 else None
            is_act = self.state in (RainActivityState.ACTIVE_RAIN, RainActivityState.RECENT_RAIN)
            return {
                "state": self.state.value,
                "is_active": is_act,
                "last_mm": self.last_mm,
                "seconds_since_rain": sec_since_increase,
                "window_s": self.window_seconds,
            }


class OllamaState(str, Enum):
    """States of the local LLM weather descriptor."""
    DISABLED = "DISABLED"
    WARMING_UP = "WARMING_UP"
    READY = "READY"
    DEGRADED = "DEGRADED"


class OllamaStateMachine:
    """Thread-safe state machine governing local Ollama LLM integration."""

    def __init__(self, enabled: bool, url: str, model: str, cache_ttl_s: int):
        self._lock = threading.RLock()
        self.state: OllamaState = OllamaState.DISABLED if not enabled else OllamaState.WARMING_UP
        self.url: str = url
        self.model: str = model
        self.cache_ttl_s: int = cache_ttl_s
        self.cached_result: Optional[Dict[str, Any]] = None
        self.cached_at: float = 0.0
        self.success_count: int = 0
        self.fallback_count: int = 0
        self.last_latency_s: Optional[float] = None
        self.last_error: Optional[str] = None

    def record_warmup_success(self, raw_reply: str) -> None:
        with self._lock:
            self.state = OllamaState.READY
            self.last_error = None

    def record_warmup_failure(self, error: str) -> None:
        with self._lock:
            self.state = OllamaState.DEGRADED
            self.last_error = error

    def record_success(self, cond: str, icon: str, desc: str, latency_s: float, now: float) -> None:
        with self._lock:
            self.state = OllamaState.READY
            self.success_count += 1
            self.last_latency_s = latency_s
            self.last_error = None
            self.cached_at = now
            self.cached_result = {"condition": cond, "icon": icon, "description": desc, "source": "ollama"}

    def record_fallback(self, error: str) -> None:
        with self._lock:
            self.state = OllamaState.DEGRADED
            self.fallback_count += 1
            self.last_error = error

    def get_cached_description(self, now: float) -> Optional[str]:
        with self._lock:
            if self.cached_result and (now - self.cached_at) < self.cache_ttl_s:
                return self.cached_result.get("description")
            return None

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "state": self.state.value,
                "model": self.model,
                "url": self.url,
                "cache_ttl_s": self.cache_ttl_s,
                "last_latency_s": self.last_latency_s,
                "success_count": self.success_count,
                "fallback_count": self.fallback_count,
                "last_error": self.last_error,
            }


class TCPServerState(str, Enum):
    """Lifecycle states of the TCP push broadcast server."""
    STOPPED = "STOPPED"
    LISTENING = "LISTENING"
    ERROR = "ERROR"


# Global state machine instances
collector_fsm = CollectorStateMachine(frequency=WX_FREQ)
rain_fsm = RainStateMachine(window_seconds=1800)
ollama_fsm = OllamaStateMachine(
    enabled=OLLAMA_ENABLED,
    url=OLLAMA_URL,
    model=OLLAMA_MODEL,
    cache_ttl_s=OLLAMA_CACHE_S,
)
tcp_server: Optional["TCPPushServer"] = None


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

    def __init__(self, port: int):
        self.port = port
        self.clients = []
        self._lock = threading.RLock()
        self.state: TCPServerState = TCPServerState.STOPPED
        self.messages_broadcast: int = 0
        self.total_clients_served: int = 0
        self.last_broadcast_time: Optional[float] = None
        self._running = True

        try:
            self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server.bind(("0.0.0.0", port))
            self._server.listen(5)
            self._server.setblocking(False)
            self.state = TCPServerState.LISTENING

            self._accept_thread = threading.Thread(
                target=self._accept_loop, daemon=True
            )
            self._accept_thread.start()
            print(f"[tcp-push] listening on port {port}")
        except Exception as e:
            self.state = TCPServerState.ERROR
            print(f"[tcp-push] failed to bind port {port}: {e}")
            raise

    def _accept_loop(self):
        while self._running:
            try:
                readable, _, _ = select.select([self._server], [], [], 1.0)
                if readable:
                    sock, addr = self._server.accept()
                    sock.setblocking(False)
                    with self._lock:
                        self.clients.append(sock)
                        self.total_clients_served += 1
                        n = len(self.clients)
                    print(f"[tcp-push] client {addr[0]}:{addr[1]} ({n} connected)")
            except Exception:
                pass

    def broadcast(self, line: bytes):
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
            self.messages_broadcast += 1
            self.last_broadcast_time = time.time()

    def stop(self):
        self._running = False
        with self._lock:
            for sock in self.clients:
                try:
                    sock.close()
                except Exception:
                    pass
            self.clients = []
            self.state = TCPServerState.STOPPED
        try:
            self._server.close()
        except Exception:
            pass

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            return {
                "state": self.state.value,
                "port": self.port,
                "active_clients": len(self.clients),
                "total_clients_served": self.total_clients_served,
                "messages_broadcast": self.messages_broadcast,
                "last_broadcast_age_s": (
                    round(now - self.last_broadcast_time, 1) if self.last_broadcast_time else None
                ),
            }


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

    rtl_433 reports rain_mm as a cumulative total (since sensor start or reset).
    Delegates to RainStateMachine while maintaining full backward compatibility."""
    state = rain_fsm.update(rain_mm)
    with _rain_state["lock"]:
        _rain_state["last_mm"] = rain_fsm.last_mm
        _rain_state["increased_at"] = rain_fsm.increased_at
    return state.is_active


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
    """Call local Ollama for natural-language description — condition & icon from rules."""
    now = time.time()

    # Condition and icon always from deterministic rules (never cached)
    rule = _simple_condition(data)
    cond = rule["condition"]
    icon = rule["icon"]

    if not OLLAMA_ENABLED:
        return {"condition": cond, "icon": icon, "description": rule["description"], "source": "rule_fallback"}

    # Cache only the Ollama description text (TTL = OLLAMA_CACHE_S)
    desc_cache = ollama_fsm.get_cached_description(now)
    if desc_cache:
        return {"condition": cond, "icon": icon, "description": desc_cache, "source": "ollama_cache"}

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

    t0 = time.time()
    source = "ollama"
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
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        desc = result.get("response", "").strip()

        # Clean up: remove quotes, take first sentence only
        desc = desc.strip('"').strip("'")
        if "." in desc:
            desc = desc.split(".")[0].strip() + "."
        if len(desc) > 120:
            desc = desc[:117] + "..."

        latency = round(time.time() - t0, 2)
        ollama_fsm.record_success(cond, icon, desc, latency, now)
        print(f"[ollama] {cond} — {desc} ({latency}s)")

    except Exception as e:
        latency = round(time.time() - t0, 2)
        ollama_fsm.record_fallback(str(e))
        print(f"[ollama] failed ({e}) — using rule-based description")
        desc = rule["description"]
        if len(desc) > 120:
            desc = desc[:117] + "..."
        source = "rule_fallback"

    return {"condition": cond, "icon": icon, "description": desc, "source": source}


def _warmup_ollama():
    """Fire a tiny request to Ollama so the model is loaded before the first reading."""
    if not OLLAMA_ENABLED:
        return
    print(f"[ollama] warming up model '{OLLAMA_MODEL}'...")
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
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
        raw = result.get("response", "").strip()
        ollama_fsm.record_warmup_success(raw)
        print(f"[ollama] warmup done — model replied: {raw[:60]}")
    except Exception as e:
        ollama_fsm.record_warmup_failure(str(e))
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
    collector_fsm.on_starting(pid=proc.pid, frequency=WX_FREQ)
    stderr_lines = []
    stderr_thread = threading.Thread(
        target=_drain_stderr,
        args=(proc.stderr, "rtl_433", stderr_lines),
        daemon=True,
    )
    stderr_thread.start()

    last_err = None
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

            collector_fsm.on_packet_received()
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
        last_err = str(e)
        print(f"[collector] Error: {e}")
    finally:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        stderr_thread.join(timeout=2)
        err_summary = "; ".join(stderr_lines[-3:]) if stderr_lines else last_err
        collector_fsm.on_retry_wait(error=err_summary)
        for l in stderr_lines:
            print(l, flush=True)


def _rtl433_loop():
    """Daemon thread: keep rtl_433 running, restarting on failure."""
    global tcp_server
    conn = sqlite3.connect(DB)
    init_db(conn)
    print(f"[collector] DB ready: {DB}")

    if TCP_PORT:
        try:
            tcp_server = TCPPushServer(TCP_PORT)
        except Exception as e:
            print(f"[tcp-push] failed to start ({e}) — continuing without")
    else:
        print("[tcp-push] disabled (set TCP_PORT to enable)")

    # Warm up Ollama model before first reading (so it's loaded in GPU)
    _warmup_ollama()

    try:
        while True:
            print("[collector] Starting rtl_433...")
            _run_rtl433(conn, tcp_server)
            print("[collector] rtl_433 exited, restarting in 10s...")
            time.sleep(10)
    finally:
        collector_fsm.on_stopped()
        if tcp_server:
            tcp_server.stop()
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

    # Observable precipitation FSM state
    d["rain_state"] = rain_fsm.state.value
    d["rain_is_active"] = rain_fsm.is_active

    # Battery state machine
    batt = d.get("battery_ok")
    if batt == 1:
        d["battery_state"] = BatteryState.OK.value
    elif batt == 0:
        d["battery_state"] = BatteryState.LOW.value
    else:
        d["battery_state"] = BatteryState.UNKNOWN.value

    # Signal freshness state machine
    if d.get("time"):
        try:
            t_str = d["time"].replace("Z", "+00:00")
            t_parsed = datetime.fromisoformat(t_str)
            if t_parsed.tzinfo is None:
                t_parsed = t_parsed.replace(tzinfo=timezone.utc)
            age_s = max(0.0, (datetime.now(timezone.utc) - t_parsed).total_seconds())
            d["sensor_age_s"] = round(age_s, 1)
            if age_s < 120:
                d["signal_state"] = SensorSignalState.LIVE.value
            elif age_s <= 300:
                d["signal_state"] = SensorSignalState.RECENT.value
            else:
                d["signal_state"] = SensorSignalState.STALE.value
        except Exception:
            d["sensor_age_s"] = None
            d["signal_state"] = SensorSignalState.UNKNOWN.value
    else:
        d["sensor_age_s"] = None
        d["signal_state"] = SensorSignalState.UNKNOWN.value

    return jsonify(d)


@app.route("/api/status")
def api_status():
    """System health and comprehensive state machine observatory (AX / DX)."""
    db = get_db()
    try:
        row = db.execute("SELECT COUNT(*), MAX(time) FROM readings").fetchone()
        readings_count = row[0] if row else 0
        last_db_time = row[1] if row else None
    except Exception:
        readings_count = 0
        last_db_time = None

    coll_status = collector_fsm.get_status()
    rain_status = rain_fsm.get_status()
    ollama_status = ollama_fsm.get_status()
    tcp_status = tcp_server.get_status() if tcp_server else {"state": "DISABLED", "port": TCP_PORT}

    sensor_info = {
        "station_id": None,
        "battery_state": BatteryState.UNKNOWN.value,
        "signal_state": SensorSignalState.UNKNOWN.value,
        "age_s": None,
        "last_seen": last_db_time,
    }
    if last_db_time:
        try:
            latest = db.execute("SELECT station_id, battery_ok, time FROM readings ORDER BY id DESC LIMIT 1").fetchone()
            if latest:
                b = latest["battery_ok"]
                sensor_info["station_id"] = latest["station_id"]
                sensor_info["battery_state"] = (
                    BatteryState.OK.value if b == 1 else (BatteryState.LOW.value if b == 0 else BatteryState.UNKNOWN.value)
                )
                t_parsed = datetime.fromisoformat(latest["time"].replace("Z", "+00:00"))
                if t_parsed.tzinfo is None:
                    t_parsed = t_parsed.replace(tzinfo=timezone.utc)
                age = max(0.0, (datetime.now(timezone.utc) - t_parsed).total_seconds())
                sensor_info["age_s"] = round(age, 1)
                if age < 120:
                    sensor_info["signal_state"] = SensorSignalState.LIVE.value
                elif age <= 300:
                    sensor_info["signal_state"] = SensorSignalState.RECENT.value
                else:
                    sensor_info["signal_state"] = SensorSignalState.STALE.value
        except Exception:
            pass

    if coll_status["state"] in (CollectorState.RECEIVING.value, CollectorState.STARTING.value):
        overall = "HEALTHY"
    elif coll_status["state"] == CollectorState.RETRY_WAITING.value:
        overall = "DEGRADED"
    else:
        overall = "WARNING"

    return jsonify({
        "status": overall,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "uptime_s": round(time.time() - _START_TIME, 1),
        "collector": coll_status,
        "rain": rain_status,
        "ollama": ollama_status,
        "tcp_push": tcp_status,
        "sensor": sensor_info,
        "database": {
            "path": DB,
            "readings_count": readings_count,
            "last_reading_time": last_db_time,
        },
    })


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
<script src="https://cdn.jsdelivr.net/npm/@sourcentis/chartjs-gauge@1.2.0/dist/chartjs-gauge.umd.js"></script>
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
    transition: background 0.3s, box-shadow 0.3s;
  }
  .status-dot.live { background: var(--green); box-shadow: 0 0 8px var(--green), 0 0 16px rgba(0,255,136,0.4); }
  .status-dot.stale { background: var(--yellow); box-shadow: 0 0 8px var(--yellow), 0 0 16px rgba(255,204,0,0.4); }
  .status-dot.reconnecting { background: var(--purple); box-shadow: 0 0 8px var(--purple), 0 0 16px rgba(189,147,249,0.4); }
  .status-dot.offline { background: var(--red); box-shadow: 0 0 8px var(--red), 0 0 16px rgba(255,51,68,0.4); }
  .status-bar .sep { color: var(--border); }

  /* network info */
  .net-info {
    position: relative; z-index: 1;
    display: flex; flex-wrap: wrap; gap: clamp(8px, 1.5vw, 16px);
    margin-bottom: clamp(10px, 1.5vw, 14px);
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

  /* telemetry pill bar */
  .telemetry-bar {
    position: relative; z-index: 1;
    display: flex; flex-wrap: wrap; align-items: center; gap: clamp(6px, 1.2vw, 12px);
    margin-bottom: clamp(16px, 2vw, 24px);
    font-family: var(--font); font-size: clamp(0.62em, 0.95vw, 0.72em);
    padding: 6px 14px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
  }
  .telemetry-item { display: flex; align-items: center; gap: 6px; color: var(--muted); }
  .telemetry-item .label { color: var(--muted); text-transform: uppercase; font-size: 0.88em; }
  .telemetry-badge {
    padding: 2px 7px; border-radius: 4px; font-weight: 700; font-size: 0.9em;
    letter-spacing: 0.04em; display: inline-block;
  }
  .telemetry-badge.ok   { background: rgba(0,255,136,0.12); color: var(--green);   border: 1px solid rgba(0,255,136,0.3); }
  .telemetry-badge.warn { background: rgba(255,204,0,0.12); color: var(--yellow);  border: 1px solid rgba(255,204,0,0.3); }
  .telemetry-badge.crit { background: rgba(255,51,68,0.12); color: var(--red);     border: 1px solid rgba(255,51,68,0.3); }
  .telemetry-badge.info { background: rgba(0,240,255,0.12); color: var(--cyan);    border: 1px solid rgba(0,240,255,0.3); }

  /* ── Cards ────────────────────────────── */
  .cards {
    position: relative; z-index: 1;
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(clamp(180px, 20vw, 240px), 1fr));
    gap: clamp(10px, 1.5vw, 16px);
    margin-bottom: clamp(20px, 3vw, 28px);
  }
  .cards:has(.card-hero) { grid-template-columns: 2fr repeat(auto-fit, minmax(clamp(160px, 18vw, 220px), 1fr)); }
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
  .card .trend {
    font-family: var(--font);
    font-size: clamp(0.7em, 1vw, 0.85em);
    margin-left: 6px;
    display: inline-block;
    transition: opacity 0.3s;
  }
  .card .trend.up { color: var(--green); }
  .card .trend.down { color: var(--red); }
  .card .trend.flat { color: var(--muted); }
  .card .fresh {
    font-family: var(--font);
    font-size: clamp(0.5em, 0.7vw, 0.6em);
    color: #3b4261;
    margin-top: 2px;
  }
  .card .range {
    font-family: var(--font);
    font-size: clamp(0.58em, 0.8vw, 0.65em); color: #3b4261;
    margin-top: 8px; padding-top: 8px;
    border-top: 1px solid var(--border);
  }

  /* hero temperature card */
  .card-hero {
    grid-column: 1 / -1;
  }
  .card-hero .main-val {
    font-size: clamp(3em, 6vw, 5em);
  }
  .card-hero .card-row {
    display: flex; align-items: baseline; gap: clamp(16px, 3vw, 32px);
    flex-wrap: wrap;
  }
  .card-hero .card-row .hero-feels {
    font-family: var(--font);
    font-size: clamp(1em, 1.5vw, 1.5em);
    color: var(--muted);
  }
  .card-hero .card-row .hero-feels .val { color: var(--text); font-weight: 600; }

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

  /* ── Gauges ────────────────────────────── */
  .gauges {
    position: relative; z-index: 1;
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(clamp(180px, 22vw, 260px), 1fr));
    gap: clamp(10px, 1.5vw, 16px);
    margin-bottom: clamp(20px, 3vw, 28px);
  }
  .gauge-box {
    background: var(--surface);
    border-radius: var(--radius);
    border: 1px solid var(--border);
    padding: clamp(10px, 1.5vw, 16px);
    text-align: center;
  }
  .gauge-box .gauge-label {
    font-family: var(--font);
    font-size: clamp(0.65em, 0.85vw, 0.72em);
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-bottom: 4px;
    text-align: left;
  }
  .gauge-box canvas {
    width: 100% !important;
    height: auto !important;
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
    <span>BAT:<strong id="battery-status">--</strong></span>
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

<div class="telemetry-bar" id="telemetry-bar">
  <div class="telemetry-item"><span class="label">SDR:</span> <span id="telem-collector" class="telemetry-badge info">STOPPED</span></div>
  <span class="sep">│</span>
  <div class="telemetry-item"><span class="label">PRECIP:</span> <span id="telem-rain" class="telemetry-badge ok">DRY</span></div>
  <span class="sep">│</span>
  <div class="telemetry-item"><span class="label">AI:</span> <span id="telem-ollama" class="telemetry-badge info">INIT</span></div>
  <span class="sep">│</span>
  <div class="telemetry-item"><span class="label">TCP:</span> <span id="telem-tcp" class="telemetry-badge info">LISTENING</span></div>
</div>

<div class="cards">
  <div class="card temp card-hero">
    <div class="icon" id="icon-temp"></div>
    <div class="label">Temperature</div>
    <div class="card-row">
      <div>
        <span class="main-val" id="temp">--<span class="unit">°C</span></span>
        <span class="trend" id="trend-temp"></span>
      </div>
      <div class="hero-feels">Feels like <span class="val" id="feels">--</span></div>
    </div>
    <div class="fresh" id="fresh-temp"></div>
    <div class="range" id="range-temp"></div>
  </div>
  <div class="card hum">
    <div class="icon" id="icon-hum"></div>
    <div class="label">Humidity</div>
    <div><span class="main-val" id="hum">--<span class="unit">%</span></span><span class="trend" id="trend-hum"></span></div>
    <div class="sub" id="comfort"></div>
    <div class="fresh" id="fresh-hum"></div>
    <div class="range" id="range-hum"></div>
  </div>
  <div class="card wind">
    <div class="icon" id="icon-wind"></div>
    <div class="label">Wind</div>
    <div style="display:flex;align-items:center;gap:10px;">
      <div class="compass" id="compass"></div>
      <div>
        <div style="font-family:var(--font);font-size:clamp(1.2em,1.8vw,1.4em);font-weight:800;" id="wind-dir">--</div>
        <div><span class="main-val" style="font-size:clamp(1em,1.5vw,1.2em);" id="wind-speed">--</span><span class="trend" id="trend-wind"></span></div>
        <div class="sub" id="beaufort"></div>
      </div>
    </div>
    <div class="fresh" id="fresh-wind"></div>
    <div class="range" id="range-wind"></div>
  </div>
  <div class="card dew">
    <div class="icon" id="icon-dew"></div>
    <div class="label">Dew Point</div>
    <div><span class="main-val" id="dew">--<span class="unit">°C</span></span><span class="trend" id="trend-dew"></span></div>
    <div class="sub" id="dew-note"></div>
    <div class="fresh" id="fresh-dew"></div>
  </div>
  <div class="card rain">
    <div class="icon" id="icon-rain"></div>
    <div class="label">Rain Today</div>
    <div><span class="main-val" style="font-size:clamp(1.8em,3vw,2.4em);" id="rain">--<span class="unit"> mm</span></span><span class="trend" id="trend-rain"></span><span id="rain-fsm-badge" class="badge" style="margin-left:8px;vertical-align:middle;display:none;"></span></div>
    <div class="rain-bar"><div class="rain-bar-fill" id="rain-bar" style="width:0%"></div></div>
    <div class="sub" style="margin-top:4px;" id="rain-total"></div>
    <div class="fresh" id="fresh-rain"></div>
  </div>
</div>

<div class="gauges">
  <div class="gauge-box"><div class="gauge-label">▸ Temperature</div><canvas id="tempGauge" height="140"></canvas></div>
  <div class="gauge-box"><div class="gauge-label">▸ Humidity</div><canvas id="humGauge" height="140"></canvas></div>
  <div class="gauge-box"><div class="gauge-label">▸ Wind Speed</div><canvas id="windGauge" height="140"></canvas></div>
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

// ── Gauges ───────────────────────────────────
function gaugeSegments(type) {
  if (type === 'temp') return {
    data: [35, 20, 20, 25], bg: ['#3b82f6','#22c55e','#eab308','#ef4444'],
    min: -10, max: 50, circ: 180, rot: -90 };
  if (type === 'hum') return {
    data: [30, 40, 30], bg: ['#ef4444','#22c55e','#3b82f6'],
    min: 0, max: 100, circ: 180, rot: -90 };
  if (type === 'wind') return {
    data: [33, 34, 33], bg: ['#22c55e','#eab308','#ef4444'],
    min: 0, max: 30, circ: 270, rot: -135 };
}

function drawGauge(id, value, unit, type, color) {
  var seg = gaugeSegments(type);
  var ctx = document.getElementById(id).getContext('2d');
  var key2 = id + '_g';

  if (window[key2]) {
    var chart = window[key2];
    chart.data.datasets[0].value = value;
    chart.update('default');
    return;
  }

  window[key2] = new Chart(ctx, {
    type: 'gauge',
    data: { datasets: [{ value: value, data: seg.data, backgroundColor: seg.bg, borderWidth: 0 }] },
    options: {
      responsive: true, maintainAspectRatio: true,
      cutout: '75%', circumference: seg.circ, rotation: seg.rot,
      gauge: {
        minValue: seg.min, maxValue: seg.max,
        valueLabel: { formatter: function(v) { return v.toFixed(1) + unit; }, color: color,
          font: { size: 14, family: 'JetBrains Mono, monospace', weight: 'bold' } }
      },
      plugins: { legend: { display: false }, tooltip: { enabled: false } }
    }
  });
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

// ── Explicit Frontend Dashboard State Machine (UX / DX / AX) ────────
const DashboardState = Object.freeze({
  INITIALIZING: 'INITIALIZING',
  LIVE: 'LIVE',
  STALE: 'STALE',
  RECONNECTING: 'RECONNECTING',
  OFFLINE: 'OFFLINE'
});

class DashboardStateMachine {
  constructor() {
    this.state = DashboardState.INITIALIZING;
    this.consecutiveFailures = 0;
    this.lastSuccessfulSync = null;
    this.render();
  }

  transition(event, payload) {
    payload = payload || {};
    var prev = this.state;
    switch (event) {
      case 'FETCH_SUCCESS':
        this.consecutiveFailures = 0;
        this.lastSuccessfulSync = Date.now();
        var ageSec = payload.ageSec != null ? payload.ageSec : 0;
        this.state = ageSec > 300 ? DashboardState.STALE : DashboardState.LIVE;
        break;

      case 'FETCH_FAILURE':
        this.consecutiveFailures++;
        if (this.consecutiveFailures === 1) {
          this.state = DashboardState.RECONNECTING;
        } else {
          this.state = DashboardState.OFFLINE;
        }
        break;

      case 'RESET':
      default:
        this.state = DashboardState.INITIALIZING;
        break;
    }

    if (prev !== this.state) {
      console.log('[dashboard-fsm]', prev, '->', this.state);
    }
    this.render(payload);
    window.wxDashboardState = this.state;
    document.body.dataset.wxState = this.state;
  }

  render(payload) {
    payload = payload || {};
    var dot = document.getElementById('status-dot');
    var text = document.getElementById('status-text');
    var ageEl = document.getElementById('age');
    if (!dot || !text) return;

    dot.className = 'status-dot ' + this.state.toLowerCase();

    switch (this.state) {
      case DashboardState.INITIALIZING:
        text.textContent = 'Connecting...';
        if (ageEl) ageEl.textContent = '—';
        break;
      case DashboardState.LIVE:
        text.textContent = 'Live';
        if (ageEl && payload.ageSec != null) {
          ageEl.textContent = payload.ageSec < 120 ? 'live' : Math.round(payload.ageSec / 60) + 'm ago';
        }
        break;
      case DashboardState.STALE:
        text.textContent = 'Stale';
        if (ageEl && payload.ageSec != null) {
          ageEl.textContent = Math.round(payload.ageSec / 60) + 'm ago';
        }
        break;
      case DashboardState.RECONNECTING:
        text.textContent = 'Reconnecting...';
        break;
      case DashboardState.OFFLINE:
        text.textContent = 'Offline';
        if (ageEl) ageEl.textContent = '—';
        break;
    }
  }
}

const dashboardFsm = new DashboardStateMachine();

// ── Refresh loop ─────────────────────────────
var lastId = 0;
var historyRows = [];
var _prev = {};
var _now = 0;

function setTrend(id, curr, prev, decimals) {
  var el = document.getElementById(id);
  if (!el) return;
  if (prev == null || curr == null) { el.textContent = ''; el.className = 'trend'; return; }
  var diff = curr - prev;
  var d = decimals != null ? diff.toFixed(decimals) : (diff > 0 ? '+' + diff.toFixed(1) : diff.toFixed(1));
  if (diff > 0.05) { el.innerHTML = '▲' + d; el.className = 'trend up'; }
  else if (diff < -0.05) { el.innerHTML = '▼' + d; el.className = 'trend down'; }
  else { el.textContent = '→'; el.className = 'trend flat'; }
}

function setFresh(id, ts) {
  var el = document.getElementById(id);
  if (!el) return;
  if (!ts) { el.textContent = ''; return; }
  var s = Math.round((Date.now() - ts) / 1000);
  if (s < 10) el.textContent = 'just now';
  else if (s < 60) el.textContent = s + 's ago';
  else el.textContent = Math.round(s / 60) + 'm ago';
}

async function refreshTelemetry() {
  try {
    var r = await fetch('/api/status');
    if (!r.ok) return;
    var s = await r.json();

    // Collector telemetry badge
    var elColl = document.getElementById('telem-collector');
    if (elColl && s.collector) {
      var st = s.collector.state;
      elColl.textContent = st;
      elColl.className = 'telemetry-badge ' + (st === 'RECEIVING' ? 'ok' : (st === 'STARTING' || st === 'RETRY_WAITING' ? 'warn' : 'crit'));
    }

    // Rain FSM badge
    var elRain = document.getElementById('telem-rain');
    if (elRain && s.rain) {
      var rst = s.rain.state;
      elRain.textContent = rst;
      elRain.className = 'telemetry-badge ' + (rst === 'ACTIVE_RAIN' ? 'crit' : (rst === 'RECENT_RAIN' ? 'warn' : 'ok'));
    }

    // AI Ollama badge
    var elAi = document.getElementById('telem-ollama');
    if (elAi && s.ollama) {
      var ost = s.ollama.state;
      elAi.textContent = ost;
      elAi.className = 'telemetry-badge ' + (ost === 'READY' ? 'ok' : (ost === 'DEGRADED' ? 'warn' : 'info'));
    }

    // TCP Push badge
    var elTcp = document.getElementById('telem-tcp');
    if (elTcp && s.tcp_push) {
      var tst = s.tcp_push.state;
      var count = s.tcp_push.active_clients || 0;
      elTcp.textContent = tst + (count > 0 ? ' (' + count + ')' : '');
      elTcp.className = 'telemetry-badge ' + (tst === 'LISTENING' ? 'info' : 'warn');
    }
  } catch(e) {}
}

async function refresh() {
  _now = Date.now();
  try {
    var r = await fetch('/api/current');
    if (!r.ok) {
      dashboardFsm.transition('FETCH_FAILURE', { status: r.status });
    } else {
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
      setTrend('trend-temp', t, _prev.t, 1);
      setFresh('fresh-temp', _now);
      _prev.t = t;
      var fl = '';
      if (t != null) {
        if (t < 0) fl = 'Freezing'; else if (t < 10) fl = 'Chilly'; else if (t < 20) fl = 'Mild'; else if (t < 28) fl = 'Warm'; else fl = 'Hot';
      }
      document.getElementById('feels').innerHTML = '<span>' + fl + '</span>';

      document.getElementById('icon-hum').textContent = humIcon(h);
      document.getElementById('hum').innerHTML = (h != null ? h : '--') + '<span class="unit">%</span>';
      setTrend('trend-hum', h, _prev.h, 0);
      setFresh('fresh-hum', _now);
      _prev.h = h;
      document.getElementById('comfort').innerHTML = comfort(h);

      document.getElementById('icon-wind').textContent = windIcon(wg);
      document.getElementById('wind-dir').textContent = windDirStr(wd);
      document.getElementById('wind-speed').innerHTML = (ws != null ? ws.toFixed(1) : '--') + ' avg / ' + (wg != null ? wg.toFixed(1) : '--') + ' gust m/s';
      setTrend('trend-wind', ws, _prev.ws, 2);
      setFresh('fresh-wind', _now);
      _prev.ws = ws;
      var bf = beaufort(ws);
      document.getElementById('beaufort').innerHTML = bf ? bf[1] + ' ' + bf[0] : '';

      document.getElementById('icon-dew').textContent = dewIcon(dp);
      document.getElementById('dew').innerHTML = (dp != null ? dp : '--') + '<span class="unit">°C</span>';
      setTrend('trend-dew', dp, _prev.dp, 1);
      setFresh('fresh-dew', _now);
      _prev.dp = dp;
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
      setTrend('trend-rain', rain, _prev.rain, 1);
      setFresh('fresh-rain', _now);
      _prev.rain = rain;
      document.getElementById('rain-bar').style.width = Math.min(100, ((rain||0) / 20) * 100) + '%';

      // Observable Rain FSM badge update
      var rainBadge = document.getElementById('rain-fsm-badge');
      if (rainBadge && d.rain_state) {
        rainBadge.style.display = 'inline-block';
        if (d.rain_state === 'ACTIVE_RAIN') {
          rainBadge.className = 'badge bad';
          rainBadge.textContent = 'Raining Now 🌧️';
        } else if (d.rain_state === 'RECENT_RAIN') {
          rainBadge.className = 'badge warn';
          rainBadge.textContent = 'Recent Rain';
        } else {
          rainBadge.className = 'badge good';
          rainBadge.textContent = 'Dry';
        }
      }

      drawCompass(wd);
      document.getElementById('station-id').textContent = d.station_id || '--';
      var batEl = document.getElementById('battery-status');
      if (batEl && d.battery_state) {
        batEl.textContent = d.battery_state;
        batEl.style.color = d.battery_state === 'LOW' ? 'var(--red)' : (d.battery_state === 'OK' ? 'var(--green)' : 'var(--muted)');
      }

      var age = (Date.now() / 1000) - (new Date(d.time + 'Z').getTime() / 1000);
      dashboardFsm.transition('FETCH_SUCCESS', { ageSec: age });

      drawGauge('tempGauge', t != null ? t : 0, '°C', 'temp', C.magenta);
      drawGauge('humGauge',  h != null ? h : 50, '%',  'hum',  C.cyan);
      drawGauge('windGauge', ws != null ? ws : 0, ' m/s', 'wind', C.green);

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
  } catch(e) {
    dashboardFsm.transition('FETCH_FAILURE', { error: e.message });
  }

  // Refresh telemetry status pills
  refreshTelemetry();

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
