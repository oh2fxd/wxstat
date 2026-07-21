# Requirements: wxstat Dashboard

**Defined:** 2026-07-21
**Core Value:** Reliable, real-time weather monitoring that never drops a reading

## v1 Requirements

### Animated Charts

- [ ] **CHRT-01**: Charts update data in-place (mutate arrays, call `chart.update()`) instead of destroy+recreate — eliminates flicker
- [ ] **CHRT-02**: New data points animate from previous point position using `animations.x.from` / `animations.y.from` callbacks
- [ ] **CHRT-03**: Pulse effect (brief opacity/glow animation) on chart container when new data arrives

### Analog Gauges

- [ ] **GAUG-01**: Temperature gauge — 180° half-doughnut with animated needle and colored zones (blue/green/orange/red)
- [ ] **GAUG-02**: Humidity gauge — 180° half-doughnut with animated needle and dry/comfort/wet color zones
- [ ] **GAUG-03**: Wind speed gauge — 270° arc with animated needle and color zones

### Current Conditions Panel

- [ ] **COND-01**: Temperature displayed as hero element (larger, primary position) with feels-like value
- [ ] **COND-02**: Today's high/low range displayed for key metrics
- [ ] **COND-03**: Trend arrows (⬆⬇➡) with delta values showing direction of change since last reading
- [ ] **COND-04**: Data freshness indicator per-metric showing time since last update

## v2 Requirements

Deferred to future release.

### Charts

- **CHRT-04**: Crosshair tooltip with shared cursor across all chart series
- **CHRT-05**: Segmented threshold zones (colored background bands on charts)
- **CHRT-06**: Scrollable history timespan selector (6h/24h/7d)

### Gauges

- **GAUG-04**: Rain rate/today gauge
- **GAUG-05**: Dual unit display (C/F, m/s/kmh)

### Conditions

- **COND-05**: Dynamic color tinting on hero based on temperature range

### Infrastructure

- **INFRA-01**: SSE data pipeline for real-time push instead of polling
- **INFRA-02**: Data decimation for large datasets using Chart.js built-in plugin

## Out of Scope

| Feature | Reason |
|---------|--------|
| Forecast or radar weather data | Out of scope for sensor-only dashboard |
| 3D chart or glass-style gauge effects | Anti-feature — degrades readability |
| Mobile app | Web-only dashboard |
| MQTT / Home Assistant integration | TCP push covers embedded needs |
| Authentication / TLS | LAN-only design |
| Docker containerization | Single-file deployment is deliberate |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| CHRT-01 | Phase 1 | Pending |
| CHRT-02 | Phase 1 | Pending |
| CHRT-03 | Phase 1 | Pending |
| GAUG-01 | Phase 1 | Pending |
| GAUG-02 | Phase 1 | Pending |
| GAUG-03 | Phase 1 | Pending |
| COND-01 | Phase 1 | Pending |
| COND-02 | Phase 1 | Pending |
| COND-03 | Phase 1 | Pending |
| COND-04 | Phase 1 | Pending |

**Coverage:**
- v1 requirements: 10 total
- Mapped to phases: 0
- Unmapped: 10 ⚠️

---
*Requirements defined: 2026-07-21*
*Last updated: 2026-07-21 after initial definition*
