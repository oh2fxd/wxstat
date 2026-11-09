#!/usr/bin/env python3
"""868 MHz Weather Station Frequency Scanner for wxstat.

Scans the 868 MHz ISM band to detect the exact transmission frequency,
signal strength (RSSI/SNR), and sensor ID of Fine Offset / weather station transmitters.
Saves the optimal tuned frequency to wx_config.json for wx_server.py.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from enum import Enum

class ScanState(str, Enum):
    IDLE = "IDLE"
    SCANNING = "SCANNING"
    LOCKED = "LOCKED"
    NO_SIGNAL = "NO_SIGNAL"
    ABORTED = "ABORTED"

# ANSI colors
GREEN = "\033[92m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wx_config.json")

DEFAULT_FREQS = [
    "868.150M",
    "868.200M",
    "868.250M",
    "868.300M",
    "868.350M",
    "868.400M",
    "868.450M",
]


def colored(text, color_code):
    return f"{color_code}{text}{RESET}" if sys.stdout.isatty() else text


def check_rtl433():
    if not shutil.which("rtl_433"):
        print(colored("❌ rtl_433 not found in PATH. Install with: sudo apt install rtl-433", RED))
        return False
    return True


def scan_single_frequency(freq, dwell_s=52, gain="20"):
    """Listen on a specific frequency for dwell_s seconds or until a weather packet arrives."""
    print(f"  {colored('→', CYAN)} Testing {colored(freq, BOLD + CYAN)} (dwell {dwell_s}s)...", end="", flush=True)

    cmd = [
        "rtl_433",
        "-Y", "classic",
        "-f", freq,
        "-s", "250k",
        "-g", str(gain),
        "-M", "level",
        "-F", "json",
    ]

    proc = None
    start = time.time()
    detected_packets = []

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)

        while (time.time() - start) < dwell_s:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
                continue

            line = line.strip()
            if not line or not line.startswith("{"):
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Accept sensor packets containing temperature or model
            if "temperature_C" in data or "model" in data:
                data["tuned_freq"] = freq
                detected_packets.append(data)
                break  # Got reading on this frequency

    except KeyboardInterrupt:
        raise
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()

    if detected_packets:
        p = detected_packets[0]
        model = p.get("model", "Sensor")
        sid = p.get("id", "N/A")
        t = p.get("temperature_C", "N/A")
        rssi = p.get("rssi", p.get("snr", "N/A"))
        print(f" {colored('✓ DETECTED!', GREEN + BOLD)} [{model} id={sid} temp={t}°C rssi={rssi}dB]")
        return p
    else:
        print(f" {colored('no signal', DIM)}")
        return None


def scan_hopping_mode(freqs, dwell_s=55, gain="20", timeout_s=300):
    """Run rtl_433 native frequency hopping across multiple frequencies."""
    print(colored(f"\n📡 Running native frequency hopping across {len(freqs)} channels...", BOLD + CYAN))
    print(colored(f"Channels: {', '.join(freqs)} (hop every {dwell_s}s, max timeout {timeout_s}s)", DIM))
    print(colored("Press Ctrl+C to abort scan.\n", DIM))

    cmd = ["rtl_433", "-Y", "classic"]
    for f in freqs:
        cmd.extend(["-f", f])
    cmd.extend(["-H", str(dwell_s), "-s", "250k", "-g", str(gain), "-M", "level", "-F", "json"])

    proc = None
    detected = []
    start = time.time()

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        while (time.time() - start) < timeout_s:
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

            if "temperature_C" in data or "model" in data:
                detected.append(data)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] {colored('MATCH:', GREEN + BOLD)} {data.get('model')} (id={data.get('id')}): temp={data.get('temperature_C')}°C hum={data.get('humidity')}% freq={data.get('freq', 'unknown')} snr={data.get('snr', 'N/A')}dB")
                break
    except KeyboardInterrupt:
        print("\nAborted by user.")
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()

    return detected


def save_config(frequency, sensor_id=None, model=None):
    """Save selected frequency to wx_config.json."""
    config = {
        "frequency": frequency,
        "calibrated_at": datetime.now().isoformat(),
        "sensor_id": sensor_id,
        "model": model,
    }
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    print(f"\n{colored('💾 Saved configuration to:', GREEN + BOLD)} {CONFIG_FILE}")
    print(f"   Frequency: {colored(frequency, CYAN + BOLD)}")


def main():
    parser = argparse.ArgumentParser(description="Scan 868 MHz band for weather station frequency offset")
    parser.add_argument("--freqs", nargs="+", default=DEFAULT_FREQS, help="Candidate frequencies to test")
    parser.add_argument("--dwell", type=int, default=52, help="Dwell time in seconds per frequency (default: 52s)")
    parser.add_argument("--gain", default="20", help="RTL-SDR tuner gain in dB (default: 20)")
    parser.add_argument("--hop", action="store_true", help="Use rtl_433 native round-robin frequency hopping")
    parser.add_argument("--save", action="store_true", help="Automatically save detected frequency to wx_config.json")
    parser.add_argument("--json", action="store_true", help="Output scan outcome in JSON format for agents/automation")
    args = parser.parse_args()

    if not args.json:
        print(colored("==========================================================", BOLD))
        print(colored("       868 MHz WX STATION FREQUENCY SCANNER & CALIBRATOR   ", BOLD + CYAN))
        print(colored("==========================================================", BOLD))

    if not check_rtl433():
        if args.json:
            print(json.dumps({"scan_state": ScanState.NO_SIGNAL.value, "error": "rtl_433 binary missing"}))
        sys.exit(1)

    if not args.json:
        print(f"Target Frequencies: {', '.join(args.freqs)}")
        print(f"Sensor burst interval for WH1080 is ~48s; testing with {args.dwell}s dwell.")
        print()

    found_packet = None

    if args.hop:
        detected = scan_hopping_mode(args.freqs, dwell_s=args.dwell, gain=args.gain)
        if detected:
            found_packet = detected[0]
    else:
        for f in args.freqs:
            try:
                pkt = scan_single_frequency(f, dwell_s=args.dwell, gain=args.gain)
                if pkt:
                    found_packet = pkt
                    break
            except KeyboardInterrupt:
                print(colored("\nScan interrupted by user.", YELLOW))
                break

    if found_packet:
        best_freq = found_packet.get("tuned_freq", found_packet.get("freq", args.freqs[0]))
        if isinstance(best_freq, (int, float)):
            best_freq = f"{best_freq / 1e6:.3f}M"

        if args.json:
            out = {
                "scan_state": ScanState.LOCKED.value,
                "best_frequency": best_freq,
                "model": found_packet.get("model", "Unknown"),
                "sensor_id": found_packet.get("id"),
                "temperature_C": found_packet.get("temperature_C"),
                "humidity": found_packet.get("humidity"),
                "saved_to_config": bool(args.save),
            }
            if args.save:
                save_config(best_freq, found_packet.get("id"), found_packet.get("model"))
            print(json.dumps(out, indent=2))
            return

        print("\n" + colored("🎯 WEATHER STATION FOUND!", GREEN + BOLD))
        print(f"   Frequency:    {colored(best_freq, CYAN + BOLD)}")
        print(f"   Model:        {found_packet.get('model', 'Unknown')}")
        print(f"   Sensor ID:    {found_packet.get('id', 'N/A')}")
        print(f"   Temperature:  {found_packet.get('temperature_C', 'N/A')} °C")
        print(f"   Humidity:     {found_packet.get('humidity', 'N/A')} %")

        if args.save:
            save_config(best_freq, found_packet.get("id"), found_packet.get("model"))
        else:
            ans = input(f"\nSave frequency {best_freq} to wx_config.json? [Y/n]: ").strip().lower()
            if ans in ("", "y", "yes"):
                save_config(best_freq, found_packet.get("id"), found_packet.get("model"))
    else:
        if args.json:
            print(json.dumps({
                "scan_state": ScanState.NO_SIGNAL.value,
                "tested_frequencies": args.freqs,
                "dwell_s": args.dwell,
            }, indent=2))
            return

        print(colored("\n⚠️  No weather station signal detected in this scan cycle.", YELLOW))
        print("Tips:")
        print("  1. Verify the transmitter has good batteries.")
        print("  2. Ensure the antenna is connected to the RTL-SDR.")
        print("  3. Try increasing dwell time with --dwell 60.")


if __name__ == "__main__":
    main()
