# Integrations: wxstat

**Last updated:** 2026-07-21

## External Services

| Service | Type | Direction | Protocol | Config |
|---------|------|-----------|----------|--------|
| rtl_433 | Subprocess (SDR decoder) | Ingest | stdin/stdout JSON lines | Hardcoded in `wx_server.py:27-34` |
| Ollama | Local LLM daemon | Outbound | HTTP REST (`/api/generate`) | `OLLAMA_URL` (default `http://localhost:11434`) |

### rtl_433

- Spawned as a subprocess via `subprocess.Popen`
- Output: newline-delimited JSON on stdout
- Stderr captured for diagnostics (printed on subprocess exit)
- Hardcoded RF parameters:
  - Frequency: 868.3 MHz
  - Sample rate: 250k
  - Gain: 20
  - Protocol: `-Y classic`
- Filter: only `Fineoffset-WHx080` model readings are inserted

### Ollama

- HTTP `POST /api/generate` with JSON body
- Timeout: 60 seconds per request
- Results cached in memory for `OLLAMA_CACHE_S` (default 300s)
- Warm-up call on startup to pre-load model into GPU
- Failure is silent — falls back to deterministic rule-based analysis

## Databases

| Database | Type | Location | Schema |
|----------|------|----------|--------|
| `wxstat.db` | SQLite (file-based) | Same dir as `wx_server.py` | One table: `readings` |

**Schema** (`readings`):

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | |
| `time` | TEXT | ISO 8601 datetime |
| `model` | TEXT | Always `Fineoffset-WHx080` |
| `station_id` | INTEGER | Sensor station ID |
| `temperature_C` | REAL | |
| `humidity` | INTEGER | |
| `wind_dir_deg` | INTEGER | |
| `wind_avg_m_s` | REAL | Converted from km/h by `to_ms()` |
| `wind_max_m_s` | REAL | Converted from km/h by `to_ms()` |
| `rain_mm` | REAL | Cumulative counter from sensor |
| `battery_ok` | INTEGER | Boolean |

## Network

| Port | Protocol | Purpose |
|------|----------|---------|
| 8085 | HTTP | Flask dashboard + REST API |
| 8081 | Raw TCP | Push server for ESP32 clients |
| 11434 | HTTP (outbound) | Ollama API (optional) |

## Auth / Security

No authentication. No TLS. Designed for LAN-only use.

## Webhook / Push

TCP push server (`TCPPushServer` class) sends newline-delimited JSON to connected raw TCP clients. Each sensor reading is broadcast to all connected sockets. Disconnected clients are pruned on each broadcast.
