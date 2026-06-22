# WX Station

Minimal weather station for Fine Offset WHx080 / WH1080 sensors decoded with
[rtl_433](https://github.com/merbanan/rtl_433) on 868.3 MHz.

**Single process** — collector, SQLite storage, Flask dashboard, TCP push, and
optional LLM weather analysis all run inside `wx_server.py`.

![screenshot](screenshot.png)

## Features

- Cyberpunk-themed web dashboard with live charts (Chart.js)
- TCP push for ESP32 / embedded displays (newline-delimited JSON)
- **Ollama-powered weather analysis** — describes current conditions with
  emoji and a one-liner
- Rule-based fallback when Ollama is unavailable (always works, zero delay)
- Incremental history API for efficient polling
- Linux & macOS support

---

## Quick Start

### 1. Install dependencies

**Linux (Debian/Ubuntu):**
```bash
sudo apt install rtl-433 python3-flask
```

**macOS:**
```bash
brew install rtl_433 librtlsdr
pip3 install flask
```

### 2. Clone & run

```bash
git clone https://github.com/<you>/wxstat.git
cd wxstat
./start.sh
```

First launch output:
```
=== WX Station (Darwin) ===
[start] → http://localhost:8085
[server] Dashboard  → http://192.168.1.130:8085
[server] TCP push   → 192.168.1.130:8081
[collector] DB ready: wxstat.db
[tcp-push] listening on port 8081
[ollama] warming up model 'qwen2.5:0.5b' ...
[ollama] Rainy — Cool with very high humidity.
[collector] 2026-06-22 19:40:39  temp=10.1°C  hum=99%  [Rainy]
```

Open **http://localhost:8085** for the dashboard.

---

## Ollama Weather Analysis

When Ollama is running locally, each reading is sent to a tiny model
(~500 MB) that classifies weather conditions and writes a one-sentence
description. The result is attached to the TCP broadcast so your ESP32
can display the right icon.

### Setup

```bash
# Mac / Linux: install & start Ollama
brew install ollama                # macOS
# or: curl -fsSL https://ollama.com/install.sh | sh   # Linux

# Pull the default model (~400 MB)
ollama pull qwen2.5:0.5b

# Start the server (usually already running as a daemon)
ollama serve
```

**That's it.** `wx_server.py` detects Ollama on `localhost:11434` and
uses it automatically.

### Configuration

| Env var | Default | Description |
|---|---|---|
| `OLLAMA_ENABLED` | `true` | Set to `false` to disable (rule-based fallback only) |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama API endpoint |
| `OLLAMA_MODEL` | `qwen2.5:0.5b` | Which model to use |
| `OLLAMA_CACHE_S` | `300` | Seconds between LLM calls (no point calling every ~60s) |

Example — use a different model:
```bash
OLLAMA_MODEL=llama3.1:8b ./start.sh
```

### How it works

- First reading after startup: warms up the model (first inference may take 5–30s)
- Subsequent readings: cached for 5 minutes, sub-second response
- If Ollama is unreachable: rule-based fallback produces the same fields instantly
- Never blocks readings — analysis failure is silent

---

## TCP Push (ESP32)

Raw TCP on port **8081** — no broker, no MQTT, no libraries. The ESP32 opens a
socket and reads newline-delimited JSON. Each sensor burst (~every 60s) produces
one line.

### Message format

```json
{
  "time": "2026-06-22 18:57:27",
  "model": "Fineoffset-WHx080",
  "id": 238,
  "temperature_C": 10.0,
  "humidity": 99,
  "wind_dir_deg": 45,
  "wind_avg_km_h": 0.0,
  "wind_max_km_h": 0.0,
  "rain_mm": 4.8,
  "battery_ok": 1,
  "condition": "Rainy",
  "icon": "🌧️",
  "description": "Cool with very high humidity and recent rain."
}
```

The last three fields (`condition`, `icon`, `description`) come from
Ollama or the rule-based fallback.

### ESP32 example (Arduino)

```cpp
#include <WiFi.h>

const char* WX_HOST = "192.168.1.130";
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
    // Parse JSON, read "icon" or "condition", draw on display
    Serial.println(line);
  }
}
```

### Test from a terminal

```bash
nc <host-ip> 8081           # prints a JSON line each time the sensor transmits
```

### Disable TCP push

```bash
TCP_PORT=0 ./start.sh
```

---

## API

| Endpoint | Description |
|---|---|
| `/` | Cyberpunk HTML dashboard |
| `/api/current` | Latest reading as JSON (includes `dew_point_C`) |
| `/api/history?limit=N&since=ID` | Last N readings, optionally since a row id |
| `/api/stats` | Today's min/max for temperature, humidity, wind |

---

## Project Structure

```
wxstat/
├── wx_server.py      # Collector + SQLite + Flask + TCP push + Ollama analysis
├── start.sh          # One-command launcher (Linux + macOS)
├── push.sh           # git add -A + commit + push helper
├── LICENSE           # MIT
├── README.md
└── wxstat.db         # SQLite database (auto-created on first run)
```

---

## Configuration Reference

All settings via environment variables:

| Env var | Default | Description |
|---|---|---|
| `HTTP_PORT` | `8085` | Flask dashboard port |
| `TCP_PORT` | `8081` | TCP push port (0 to disable) |
| `OLLAMA_ENABLED` | `true` | Enable Ollama analysis |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama API |
| `OLLAMA_MODEL` | `qwen2.5:0.5b` | Ollama model name |
| `OLLAMA_CACHE_S` | `300` | Seconds between LLM calls |

Frequency and gain are hardcoded in `RTL_CMD` (line 27 of `wx_server.py`).
Edit if your sensor uses a different frequency (common: 433.92, 915 MHz).

---

## Troubleshooting

**`[ollama] failed (HTTP Error 404: Not Found)`**
→ `ollama pull qwen2.5:0.5b` — the default model isn't downloaded yet.

**`[ollama] failed (Connection refused)`**
→ Ollama isn't running. Start it with `ollama serve` or launch the Mac app.

**`[ollama] failed (timed out)`**
→ The model is loading into GPU. The warm-up handles this on startup,
but very large models may take longer than 60s. Use a smaller model or
bump the timeout in `wx_server.py`.

**`usb_claim_interface error -6`** (Linux)
→ `start.sh` handles kernel module conflicts. If it persists, unplug/replug
the SDR stick or run: `sudo rmmod dvb_usb_rtl28xxu`

**No data after 5 minutes**
→ Sensor batteries dead, out of range, or wrong frequency. Scan with:
`rtl_433 -f 433.92M -s 250k -g 20`

---

## License

MIT — see [LICENSE](LICENSE)
