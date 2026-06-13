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

Opens `http://localhost:8080`. The collector starts automatically as a
background thread — it waits for sensor bursts (typically every 48–60
seconds) and shows data as soon as they arrive.

TCP push for ESP32 is on by default at port **8081**.

### Direct start
```bash
python3 wx_server.py    # collector + dashboard + TCP push
```

## Project structure

```
wxstat/
├── wx_server.py      # rtl_433 collector + SQLite + Flask dashboard + TCP push
├── start.sh          # One-command launcher (Linux + macOS)
├── push.sh           # git commit + push helper
└── wxstat.db         # SQLite database (auto-created)
```

## API

| Endpoint | Description |
|---|---|
| `/` | Dashboard (HTML) |
| `/api/current` | Latest reading as JSON |
| `/api/history?limit=N&since=ID` | Last N readings, optionally since a given row id (incremental) |
| `/api/stats` | Today's min/max |

## Frequency notes

The sensor was found at **868.3 MHz**. If yours is different, edit
`RTL_CMD` in `wx_server.py`. Common alternatives: 433.92, 915 MHz.

## TCP push (ESP32)

The server pushes readings over raw TCP on port **8081** by default —
no broker, no libraries needed. ESP32 just opens a socket and reads
newline-delimited JSON lines.

Disable with `TCP_PORT=0 python3 wx_server.py`.

### Protocol
Each reading is a single JSON line followed by `\n`. Clients connect, read, and
receive a new line each time the sensor transmits (every ~60s). The format
matches what `rtl_433` emits:

```json
{"time":"2026-06-12 19:36:52","model":"Fineoffset-WHx080","id":238,"temperature_C":16.5,"humidity":58,"wind_dir_deg":225,"wind_avg_km_h":0.0,"wind_max_km_h":1.224,"rain_mm":4.2,"battery_ok":1}
```

### ESP32 example (Arduino)
```cpp
#include <WiFi.h>

const char* WX_HOST = "192.168.1.10";
const uint16_t WX_PORT = 8081;

WiFiClient client;

void setup() {
  Serial.begin(115200);
  WiFi.begin("SSID", "password");
  while (WiFi.status() != WL_CONNECTED) delay(500);
  client.connect(WX_HOST, WX_PORT);
}

void loop() {
  if (!client.connected()) {
    client.connect(WX_HOST, WX_PORT);
    delay(1000);
    return;
  }
  while (client.available()) {
    String line = client.readStringUntil('\n');
    Serial.println(line);  // parse JSON here
  }
}
```

### Test from a terminal
```bash
nc <pi-ip> 8081       # prints a new JSON line every ~60s
```

## Troubleshooting

**`usb_claim_interface error -6`** — kernel drivers grabbed the SDR.
`start.sh` handles this on Linux. If it persists, unplug/replug the stick
or run: `sudo rmmod dvb_usb_rtl28xxu`

**No data after 5 minutes** — sensor batteries may be dead, or it's on
a different frequency. Scan with: `rtl_433 -f 433.92M -s 250k -g 20`