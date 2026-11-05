#!/usr/bin/env python3
"""RTL-SDR & 868MHz Weather Data Diagnostic & Monitor Tool for wxstat.

Checks if RTL-SDR is connected, responding, and receiving Fine Offset WHx080 sensor data.
"""

import argparse
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import unicodedata
from datetime import datetime

# ANSI Color Codes
GREEN = "\033[92m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wxstat.db")


def read_db_readings(db_path=DB_FILE, limit=1):
    """Read the latest N weather readings from SQLite database."""
    if not os.path.exists(db_path):
        print(colored(f"❌ Database file '{db_path}' not found.", RED))
        return []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM readings ORDER BY id DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
        conn.close()

        readings = []
        for r in reversed(rows):
            readings.append({
                "time": r["time"],
                "model": r["model"] or "Fineoffset-WHx080",
                "id": r["station_id"],
                "temperature_C": r["temperature_C"],
                "humidity": r["humidity"],
                "wind_dir_deg": r["wind_dir_deg"],
                "wind_avg_km_h": round(r["wind_avg_m_s"] * 3.6, 1) if r["wind_avg_m_s"] is not None else 0.0,
                "wind_max_km_h": round(r["wind_max_m_s"] * 3.6, 1) if r["wind_max_m_s"] is not None else 0.0,
                "rain_mm": r["rain_mm"],
                "battery_ok": r["battery_ok"],
                "mic": "SQLite DB",
            })
        return readings
    except Exception as e:
        print(colored(f"❌ Failed to query database: {e}", RED))
        return []


def visual_width(text):
    """Calculate visible width of text in terminal columns (excluding ANSI codes and variation selectors)."""
    clean_text = re.sub(r"\033\[[0-9;]*m", "", text)
    w = 0
    for char in clean_text:
        if char == "\ufe0f":
            continue
        if unicodedata.east_asian_width(char) in ("F", "W"):
            w += 2
        else:
            w += 1
    return w


def make_box_line(left_content, right_content="", inner_width=56):
    """Format a single box row with exact inner width padding."""
    l_w = visual_width(left_content)
    r_w = visual_width(right_content)
    pad = inner_width - l_w - r_w
    if pad < 0:
        pad = 0
    return f"│ {left_content}{' ' * pad}{right_content} │"


def supports_color():
    """Check if stdout supports color."""
    if "--no-color" in sys.argv:
        return False
    return sys.stdout.isatty()


def colored(text, color_code):
    return f"{color_code}{text}{RESET}" if supports_color() else text


def deg_to_cardinal(deg):
    """Convert degrees to cardinal wind direction."""
    if deg is None:
        return "N/A"
    cardinals = [
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"
    ]
    idx = round(deg / 22.5) % 16
    return cardinals[idx]


def dew_point(t, h):
    """Magnus formula for dew point."""
    if t is None or h is None:
        return None
    a, b = 17.27, 237.7
    gamma = (a * t) / (b + t) + math.log(h / 100.0)
    return round((b * gamma) / (a - gamma), 1)


def check_rtl433_binary():
    """Check if rtl_433 binary is installed and executable."""
    path = shutil.which("rtl_433")
    if not path:
        print(colored("❌ ERROR: rtl_433 is not installed or not in PATH.", RED))
        print("Install it using: sudo apt install rtl-433  (Linux) or brew install rtl_433 (macOS)")
        return False
    return True


def check_rtlsdr_hardware(frequency="868.3M"):
    """Probe RTL-SDR hardware response via rtl_433."""
    print(colored("🔍 Checking RTL-SDR Hardware...", BOLD))
    cmd = ["rtl_433", "-Y", "classic", "-f", frequency, "-s", "250k", "-g", "20", "-T", "2"]
    
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        _, stderr = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        _, stderr = proc.communicate()
    except Exception as e:
        print(colored(f"❌ Failed to run rtl_433: {e}", RED))
        return False, None

    # Analyze stderr output for tuner detection
    tuner_info = None
    for line in stderr.splitlines():
        if "Found" in line and "tuner" in line:
            tuner_info = line.strip()
            break
        elif "exact sample rate" in line.lower():
            tuner_info = tuner_info or line.strip()

    if "No supported devices found" in stderr or "Failed to open" in stderr:
        print(colored("❌ RTL-SDR device NOT found or not responding!", RED))
        print(colored(f"Details from rtl_433:\n{stderr.strip()}", DIM))
        return False, None

    if tuner_info:
        print(colored(f"✓ RTL-SDR Hardware Responding: {tuner_info}", GREEN + BOLD))
    else:
        print(colored("✓ RTL-SDR Hardware detected and initialized successfully.", GREEN + BOLD))
    
    return True, tuner_info


def format_weather_card(data, packet_num):
    """Format weather reading into a clean, neat ASCII console box with exact alignment."""
    t_c = data.get("temperature_C")
    t_f = (t_c * 9 / 5 + 32) if t_c is not None else None
    hum = data.get("humidity")
    dp = dew_point(t_c, hum)
    
    w_dir_deg = data.get("wind_dir_deg")
    w_cardinal = deg_to_cardinal(w_dir_deg)
    w_avg_kmh = data.get("wind_avg_km_h", 0.0)
    w_avg_ms = round(w_avg_kmh / 3.6, 1) if w_avg_kmh is not None else 0.0
    w_max_kmh = data.get("wind_max_km_h", 0.0)
    w_max_ms = round(w_max_kmh / 3.6, 1) if w_max_kmh is not None else 0.0
    
    rain = data.get("rain_mm")
    batt_ok = data.get("battery_ok")
    batt_str = colored("OK (1)", GREEN) if batt_ok == 1 else colored("LOW (0)", RED)
    
    timestamp = data.get("time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    model = data.get("model", "Unknown Sensor")
    sensor_id = data.get("id", "N/A")
    mic = data.get("mic", "N/A")

    box_top    = "┌──────────────────────────────────────────────────────────┐"
    box_bottom = "└──────────────────────────────────────────────────────────┘"
    box_sep    = "├──────────────────────────────────────────────────────────┤"

    title_left = colored(f"📡 WXSTAT SENSOR READING #{packet_num}", BOLD + CYAN)
    
    time_left = f"{colored('Time:', DIM)} {timestamp}"
    model_right = f"{colored('Model:', DIM)} {model}"

    id_left = f"{colored('Sensor ID:', DIM)} {sensor_id}"
    batt_right = f"{colored('Battery:', DIM)} {batt_str}"

    temp_str = f"{t_c:.1f}°C ({t_f:.1f}°F)" if t_c is not None else "N/A"
    dp_str = f"{dp:.1f}°C" if dp is not None else "N/A"
    temp_left = f"{colored('🌡️  Temperature:', BOLD)} {temp_str}"
    dp_right = f"{colored('Dew Point:', DIM)} {dp_str}"

    hum_str = f"{hum}%" if hum is not None else "N/A"
    rain_str = f"{rain:.1f} mm" if rain is not None else "N/A"
    hum_left = f"{colored('💧 Humidity:', BOLD)}    {hum_str}"
    rain_right = f"{colored('Rain Total:', DIM)} {rain_str}"

    wind_avg_left = f"{colored('💨 Wind Speed:', BOLD)}   {w_avg_ms:.1f} m/s ({w_avg_kmh:.1f} km/h)"
    wind_max_left = f"{colored('🌬️  Wind Gust:', BOLD)}    {w_max_ms:.1f} m/s ({w_max_kmh:.1f} km/h)"
    
    dir_str = f"{w_dir_deg}° ({w_cardinal})" if w_dir_deg is not None else "N/A"
    wind_dir_left = f"{colored('🧭 Wind Dir:', BOLD)}     {dir_str}"
    crc_right = f"{colored('CRC/MIC:', DIM)} {mic}"

    lines = [
        box_top,
        make_box_line(title_left),
        box_sep,
        make_box_line(time_left, model_right),
        make_box_line(id_left, batt_right),
        box_sep,
        make_box_line(temp_left, dp_right),
        make_box_line(hum_left, rain_right),
        box_sep,
        make_box_line(wind_avg_left),
        make_box_line(wind_max_left),
        make_box_line(wind_dir_left, crc_right),
        box_bottom,
    ]
    return "\n".join(lines)


def monitor_weather_data(frequency="868.3M", once=False, timeout=None):
    """Run rtl_433 live receiver and format decoded JSON weather packets."""
    print("\n" + colored(f"📡 Listening for weather data on {frequency}...", BOLD + CYAN))
    print(colored("Press Ctrl+C to stop.\n", DIM))

    cmd = [
        "rtl_433",
        "-Y", "classic",
        "-f", frequency,
        "-s", "250k",
        "-g", "20",
        "-F", "json",
    ]

    proc = None
    packet_count = 0
    start_time = time.time()

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        while True:
            if timeout and (time.time() - start_time) > timeout:
                print(colored(f"\n⏱️  Timeout reached ({timeout}s). Stopping listener.", YELLOW))
                break

            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue

            line = line.strip()
            if not line or not line.startswith("{"):
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            packet_count += 1
            print(format_weather_card(data, packet_count))
            print()

            if once:
                print(colored("✓ Valid packet received (--once flag set). Exiting.", GREEN + BOLD))
                break

    except KeyboardInterrupt:
        print(colored("\n🛑 Interrupted by user. Exiting cleanly.", YELLOW))
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()

    return packet_count


def show_db_mode(limit=1):
    """Show stored weather readings from wxstat.db."""
    print(colored(f"\n🗄️  Reading latest {limit} record(s) from SQLite database (wxstat.db)...", BOLD + CYAN))
    readings = read_db_readings(limit=limit)
    if not readings:
        print(colored("No records found in database.", YELLOW))
        return
    for idx, data in enumerate(readings, 1):
        print(format_weather_card(data, idx))
        print()


def main():
    parser = argparse.ArgumentParser(description="RTL-SDR 868MHz Weather Station Receiver Diagnostic & Monitor")
    parser.add_argument("-f", "--freq", default="868.3M", help="Frequency to listen on (default: 868.3M)")
    parser.add_argument("--once", action="store_true", help="Exit after receiving 1 valid weather reading")
    parser.add_argument("-t", "--timeout", type=int, default=None, help="Stop listening after N seconds")
    parser.add_argument("--db", action="store_true", help="Show latest reading from SQLite database instead of live SDR capture")
    parser.add_argument("--scan", action="store_true", help="Scan 868MHz band to find weather station frequency")
    parser.add_argument("--dwell", type=int, default=52, help="Dwell time per channel during --scan (default: 52s)")
    parser.add_argument("--last", type=int, default=1, help="Number of recent records to show in --db mode (default: 1)")
    args = parser.parse_args()

    print(colored("================================───────────────────────────", BOLD))
    print(colored("         WXSTAT RTL-SDR & 868MHz HARDWARE CHECK         ", BOLD + CYAN))
    print(colored("================================───────────────────────────", BOLD))

    if args.scan:
        import scan_wx
        sys.argv = [sys.argv[0], "--dwell", str(args.dwell)]
        scan_wx.main()
        return

    if args.db:
        show_db_mode(limit=args.last)
        return

    if not check_rtl433_binary():
        sys.exit(1)

    hw_ok, stderr_msg = check_rtlsdr_hardware(frequency=args.freq)
    if not hw_ok:
        print(colored("\nℹ️  Falling back to SQLite database view (wxstat.db)...", CYAN))
        show_db_mode(limit=args.last)
        return

    count = monitor_weather_data(frequency=args.freq, once=args.once, timeout=args.timeout)
    
    if count == 0 and not args.once:
        print(colored("ℹ️  No packets received during the session.", YELLOW))


if __name__ == "__main__":
    main()
