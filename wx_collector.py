#!/usr/bin/env python3
"""Collect weather data from rtl_433 and store in SQLite.

Optional TCP push server for direct ESP32 connection:
    TCP_PORT=8081 python3 wx_collector.py &
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
from datetime import datetime, timezone

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wxstat.db")

RTL_CMD = [
    "rtl_433",
    "-Y", "classic",
    "-f", "868.3M",
    "-s", "250k",
    "-g", "20",
    "-F", "json",
]

TCP_PORT = int(os.environ.get("TCP_PORT", 0))


# ── Database ────────────────────────────────────

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


# ── TCP push server (optional, for ESP32) ───────

class TCPPushServer:
    """Listens on a TCP port and pushes JSON readings to connected clients.

    Each reading is sent as a newline-delimited JSON object.
    Clients just open a raw TCP socket and read lines."""

    def __init__(self, port):
        self.port = port
        self.clients = []          # list of socket objects
        self.lock = threading.Lock()
        self._running = True

        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("0.0.0.0", port))
        self.server.listen(5)
        self.server.setblocking(False)

        self.thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.thread.start()
        print(f"[tcp-push] listening on port {port} (connect ESP32 here)")

    def _accept_loop(self):
        while self._running:
            try:
                readable, _, _ = select.select([self.server], [], [], 1.0)
                if readable:
                    sock, addr = self.server.accept()
                    sock.setblocking(False)
                    with self.lock:
                        self.clients.append(sock)
                    # Send latest reading immediately if available
                    print(f"[tcp-push] client connected: {addr[0]}:{addr[1]} "
                          f"({len(self.clients)} connected)")
            except Exception:
                pass

    def broadcast(self, line):
        """Send a line to all connected clients. Prune dead ones."""
        with self.lock:
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
        with self.lock:
            for sock in self.clients:
                try:
                    sock.close()
                except Exception:
                    pass
            self.clients = []
        try:
            self.server.close()
        except Exception:
            pass


# ── rtl_433 process management ──────────────────

def drain_stderr(pipe, prefix, lines_out):
    """Read stderr lines into a list for later printing (daemon thread)."""
    for line in pipe:
        line = line.strip()
        if line:
            lines_out.append(f"[{prefix}] {line}")


def run_rtl433(conn, tcp):
    """Run rtl_433 in a subprocess, yield parsed JSON messages."""
    proc = subprocess.Popen(
        RTL_CMD, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    stderr_lines = []
    stderr_thread = threading.Thread(
        target=drain_stderr,
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

            # TCP push to ESP32 / other clients
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


# ── Main loop ───────────────────────────────────

def main():
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
            run_rtl433(conn, tcp)
            print("[collector] rtl_433 exited, restarting in 10s...")
            time.sleep(10)
    finally:
        if tcp:
            tcp.stop()
        conn.close()


if __name__ == "__main__":
    main()
