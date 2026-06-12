#!/bin/bash
set -e
cd "$(dirname "$0")"
OS="$(uname -s)"

echo "=== WX Station (${OS}) ==="

# ── OS-specific setup ─────────────────────────
if [ "$OS" = "Darwin" ]; then
    # macOS: assume rtl_433 + librtlsdr installed via brew
    if ! command -v rtl_433 &>/dev/null; then
        echo "[start] rtl_433 not found. Install with: brew install rtl_433"
        exit 1
    fi
    RTL_BIN="rtl_433"
else
    # Linux: free kernel modules from SDR
    pkill rtl_433 2>/dev/null || true
    for mod in dvb_usb_rtl28xxu dvb_usb_af9015 dvb_usb_v2 dvb_core rtl2832_sdr rtl2832; do
        sudo rmmod "$mod" 2>/dev/null || true
    done
    sudo tee /etc/modprobe.d/wxstat-temp.conf <<'KMODEOF' >/dev/null
install dvb_usb_rtl28xxu /bin/true
install dvb_usb_af9015 /bin/true
install dvb_usb_v2 /bin/true
install dvb_core /bin/true
install rtl2832_sdr /bin/true
install rtl2832 /bin/true
KMODEOF
    sleep 1
fi

# ── Flask ─────────────────────────────────────
python3 -c "import flask" 2>/dev/null || pip3 install flask

# ── Collector ─────────────────────────────────
pkill -f "wx_collector.py" 2>/dev/null || true
echo "[start] Collector (868.3 MHz)..."
python3 wx_collector.py &
COLLECTOR_PID=$!
echo "[start] PID: $COLLECTOR_PID"

# ── Wait for data ─────────────────────────────
echo "[start] Waiting for sensor data..."
for i in $(seq 1 180); do
    ROWS=$(sqlite3 wxstat.db "SELECT count(*) FROM readings" 2>/dev/null || echo 0)
    if [ "$ROWS" -gt 0 ]; then
        echo "[start] $ROWS reading(s) in DB"
        break
    fi
    sleep 3
    kill -0 $COLLECTOR_PID 2>/dev/null || { echo "[start] Collector died!"; exit 1; }
done

# ── Web server ────────────────────────────────
echo "[start] → http://localhost:8080"
python3 wx_server.py

# ── Cleanup ───────────────────────────────────
kill $COLLECTOR_PID 2>/dev/null; wait $COLLECTOR_PID 2>/dev/null
[ "$OS" != "Darwin" ] && sudo rm -f /etc/modprobe.d/wxstat-temp.conf
echo "[start] Done."
