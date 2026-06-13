#!/bin/bash
set -e
cd "$(dirname "$0")"
OS="$(uname -s)"

echo "=== WX Station (${OS}) ==="

# ── OS-specific setup ─────────────────────────
if [ "$OS" = "Darwin" ]; then
    if ! command -v rtl_433 &>/dev/null; then
        echo "[start] rtl_433 not found. Install with: brew install rtl_433"
        exit 1
    fi
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

# ── Start server (collector + dashboard) ───────
echo "[start] → http://localhost:8080"
python3 wx_server.py

# ── Cleanup ───────────────────────────────────
[ "$OS" != "Darwin" ] && sudo rm -f /etc/modprobe.d/wxstat-temp.conf
echo "[start] Done."
