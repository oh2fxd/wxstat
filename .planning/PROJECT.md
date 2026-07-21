# wxstat

## What This Is

A minimal weather station dashboard for Fine Offset WHx080 sensors. It decodes SDR signals via `rtl_433`, stores readings in SQLite, and serves a web dashboard with live charts, a raw TCP push feed for ESP32 displays, and optional Ollama-powered weather descriptions. Runs as a single Python process.

## Core Value

Reliable, real-time weather monitoring that never drops a reading — the collector always writes data regardless of dashboard, analysis, or network issues.

## Requirements

### Validated

- ✓ Sensor data collection via rtl_433 subprocess — existing
- ✓ SQLite storage with WAL mode — existing
- ✓ Flask dashboard with Chart.js line charts — existing
- ✓ TCP push server for ESP32 clients — existing
- ✓ Ollama integration for AI weather analysis (with rule-based fallback) — existing
- ✓ REST API endpoints (`/api/current`, `/api/history`, `/api/stats`) — existing
- ✓ Rain detection and cumulative rain delta logic — existing
- ✓ Graceful degradation on external service failure — existing

### Active

- [ ] **DASH-01**: Animated, smoother chart transitions on existing Chart.js visualizations
- [ ] **DASH-02**: Analog-style gauge/dial widgets for temperature, humidity, wind speed, and rain
- [ ] **DASH-03**: Prominent current conditions panel showing latest readings at a glance

### Out of Scope

- Mobile app — web-only dashboard
- MQTT or Home Assistant integration — TCP push covers embedded needs for now
- Authentication/TLS — designed for LAN use
- Docker containerization — single-file deployment is deliberate
- Data export or long-term trends — deferring to future milestone

## Context

Personal weather station project using a USB RTL-SDR dongle to decode Fine Offset WHx080 sensor transmissions on 868.3 MHz. Currently a 1169-line monolith (`wx_server.py`) with collector, database, Flask server, TCP push, and Ollama analysis all in one file. The dashboard HTML/CSS/JS is embedded as a Python raw string (~540 lines). Technical debt includes no tests, no dependency pinning, and no type annotations.

## Constraints

- **Compatibility**: Must continue working with existing ESP32 TCP push clients (newline-delimited JSON format is fixed)
- **Deployment**: Must remain runnable as `python3 wx_server.py` — no build step or container required
- **Resilience**: Dashboard changes must never block sensor collection or data storage

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Keep dashboard inline in wx_server.py | Avoids adding file-serving complexity; single-file deployment stays | — Pending |
| Use Chart.js for gauges also | No new JS dependency needed (already using Chart.js from CDN) | — Pending |

---
*Last updated: 2026-07-21 after initialization*
