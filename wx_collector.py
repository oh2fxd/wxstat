#!/usr/bin/env python3
"""Collect weather data from rtl_433 and store in SQLite."""
import json
import os
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
            data.get("wind_avg_m_s"),
            data.get("wind_max_m_s"),
            data.get("rain_mm"),
            data.get("battery_ok"),
        ),
    )
    conn.commit()


def reader(pipe, prefix, lines_out):
    for line in pipe:
        line = line.strip()
        if line:
            lines_out.append(f"[{prefix}] {line}")


def main():
    conn = sqlite3.connect(DB)
    init_db(conn)
    print(f"[collector] DB ready: {DB}")

    while True:
        print("[collector] Starting rtl_433...")
        proc = subprocess.Popen(
            RTL_CMD, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        stderr_lines = []
        stderr_t = threading.Thread(
            target=reader, args=(proc.stderr, "rtl_433", stderr_lines), daemon=True
        )
        stderr_t.start()

        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("model") == "Fineoffset-WHx080":
                    insert(conn, msg)
                    temp = msg.get("temperature_C", "?")
                    hum = msg.get("humidity", "?")
                    print(f"[collector] {msg['time']}  temp={temp}C  hum={hum}%")
        except Exception as e:
            print(f"[collector] Error: {e}")
        finally:
            proc.kill()
            proc.wait()
            stderr_t.join(timeout=2)
            for l in stderr_lines:
                print(l, flush=True)

        print("[collector] rtl_433 exited, restarting in 10s...")
        time.sleep(10)


if __name__ == "__main__":
    main()
