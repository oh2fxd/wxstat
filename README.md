# WX Station

Minimal weather station dashboard for Fine Offset WHx080 / WH1080 sensors decoded
with [rtl_433](https://github.com/merbanan/rtl_433) on 868.3 MHz.

![screenshot](screenshot.png)

## Hardware

- **RTL-SDR** stick (RTL2832U)
- **Weather sensor**: Fine Offset WHx080 (sold under many brands), transmits OOK PWM at 868.3 MHz
- Antenna cut to **17.3 cm** (quarter-wave for 868 MHz)

## Install

### Linux (Debian/Ubuntu)
```bash
sudo apt install rtl-433 python3-flask sqlite3
git clone <this-repo>
cd wxstat
```

### macOS
```bash
brew install rtl_433 librtlsdr
pip3 install flask
git clone <this-repo>
cd wxstat
```

## Usage

```bash
./start.sh
```

Opens `http://localhost:8080`. The collector waits for sensor bursts
(typically every 48–60 seconds) and starts showing data automatically.

### Manual start
```bash
python3 wx_collector.py &    # background: captures and stores
python3 wx_server.py          # foreground: web dashboard
```

## Project structure

```
wxstat/
├── wx_collector.py   # rtl_433 → SQLite data collector
├── wx_server.py      # Flask web dashboard + JSON API
├── start.sh          # One-command launcher (Linux + macOS)
├── push.sh           # git commit + push helper
└── wxstat.db         # SQLite database (auto-created)
```

## API

| Endpoint | Description |
|---|---|
| `/` | Dashboard (HTML) |
| `/api/current` | Latest reading as JSON |
| `/api/history?limit=N` | Last N readings (default 288) |
| `/api/stats` | Today's min/max |

## Frequency notes

The sensor was found at **868.3 MHz**. If yours is different, edit
`RTL_CMD` in `wx_collector.py`. Common alternatives: 433.92, 915 MHz.

## Troubleshooting

**`usb_claim_interface error -6`** — kernel drivers grabbed the SDR.
`start.sh` handles this on Linux. If it persists, unplug/replug the stick
or run: `sudo rmmod dvb_usb_rtl28xxu`

**No data after 5 minutes** — sensor batteries may be dead, or it's on
a different frequency. Scan with: `rtl_433 -f 433.92M -s 250k -g 20`

## Next (planned)

- MQTT bridge
- Wind direction needle plot
- Export to CSV
