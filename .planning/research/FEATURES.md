# Feature Landscape: Weather Station Dashboard Visualizations

**Project:** wxstat — Fine Offset WHx080 Dashboard
**Domain:** Self-hosted weather station web dashboard (live data, animated charts, gauges, current conditions)
**Researched:** 2026-07-21
**Mode:** Ecosystem (Features dimension)

## Context

This research covers three new feature areas for an existing single-page dashboard that already shows:
- 4 Chart.js line charts (temperature, humidity, wind, rain) with 24h history
- 5 metric cards (temp, humidity, wind+compass, dew point, rain) with emoji icons
- Status bar, network info bar, rain progress bar
- A working SVG wind compass

The existing dashboard has a cyberpunk dark theme (cyan/magenta/green/purple accents) and polls `/api/current` + `/api/history` every 60 seconds.

---

## 1. Animated Chart Interactions (DASH-01)

### Current State

Charts are re-created entirely on each 60s refresh — `drawChart()` calls `chart.destroy()` then `new Chart(...)` every cycle. This is the single biggest issue. Animation is set to `duration: 400` with `tension: 0.35` (Bézier smoothing) and gradient fill. Tooltips exist but are basic (mode: 'index', crosshair disabled).

### Table Stakes

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **In-place data updates** — mutate `chart.data.datasets[0].data` and call `chart.update()` instead of destroy+recreate | Destroy+recreate causes visible flicker, loses animation continuity, and is wasteful | **Low** | Single biggest UX improvement. Chart.js v4 data arrays are live references — mutate in place, call `.update()`. [chart.update docs](https://www.chartjs.org/docs/latest/configuration/animations.html) |
| **Smooth new-point transition** — new data points animate from the previous point's position, not from zero (avoiding the "line drops to bottom then rises" artifact) | Without this, every new point visually disconnects from the prior data — looks broken | **Medium** | Requires `animations.x.from` and `animations.y.from` callbacks pointing to the penultimate element's pixel position. Standard pattern documented in Chart.js issues + SO. See [SO #79134989](https://stackoverflow.com/questions/79134989/how-to-avoid-chartjs-line-coming-down-when-adding-new-value) |
| **Crosshair tooltip** — shared tooltip across all series showing value at the nearest x position | Users hover to compare values across metrics | **Low** | Already partially implemented (`mode: 'index'`) but should use `mode: 'nearest'` with `intersect: false` for true crosshair feel |
| **Animation cadence aligned to update frequency** — 400ms animation on 60s poll is fine; shorter intervals should reduce/disable animation | 60s poll makes 400ms animation acceptable. If poll frequency increases, chart update must not fight with animation | **Low** | Config: if poll < 10s, set `animation.duration: 200`; if < 5s, `chart.update('none')` |
| **Stable Y-axis scale** — prevent axis rescaling on every update (or animate the scale transition) | Without this, every new data point that exceeds current Y-max causes an abrupt axis jump | **Medium** | Use `suggestedMin`/`suggestedMax` or a fixed range with headroom. If dynamic, animate with transition mode `'active'` |

### Differentiators

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Pulse effect on new data** — brief CSS animation (glow/scale pulse) on the chart container when `chart.update()` fires | Creates a tangible "something changed" signal without requiring user to track the axes | **Low** | Implemented in [Digital Urban MQTT dashboard](https://www.digitalurban.org/blog/2024/06/27/enhancing-live-weather-monitoring-with-mqtt-and-chart-js/) — a brief opacity pulse triggered by a `data-updated` class toggle |
| **Animated gradient fill** — gradient fill that subtly shifts color based on data range (e.g., hotter temps shift the gradient toward red) | Reinforces the weather context; turns a static fill into an ambient signal | **Medium** | Requires `chart.update()` then re-computing the gradient canvas pattern. Could use `beforeDraw` plugin hook |
| **Segmented threshold zones** — colored background bands on charts (e.g., red zone above 35°C, blue below 0°C) | Users instantly see if current readings are in warning ranges without reading the Y-axis | **Medium** | Chart.js `afterDraw` or a custom plugin drawing rectangular regions. Common in weather dashboards |
| **Decimation for large datasets** — Chart.js decimation plugin to down-sample dense data for rendering performance | 24h of 1-min readings = 1440 points. Still fine, but if history window grows to 7d (10080 pts), decimation becomes essential | **Low** | Chart.js has a built-in [`decimation` plugin](https://www.chartjs.org/docs/latest/configuration/decimation.html) with LTTB algorithm. Just enable it |
| **Scrollable history timespan selector** — buttons to toggle between 6h / 24h / 7d (re-fetch and update data array) | Lets users choose context depth without leaving the dashboard | **Medium** | Requires backend filtering support (`/api/history?since=<timestamp>`) + client state for active range |

### Anti-Features

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Charts that re-create on every tick** (current code) | Causes visual flicker, memory churn, loses animation state. Every chart instance takes ~50-100KB | Mutate data arrays, call `chart.update()` |
| **3D chart effects** | Distorts data perception, accessibility nightmare, no added insight | Use layered 2D with clear color encoding |
| **Pie charts** for weather data | Ineffective for time-series weather context. Users need trends, not composition | Use line/area charts or gauges |
| **Full rebuild of dashboard DOM** on every refresh | Harder to maintain, loses CSS transition state | Keep DOM stable; only update text content and chart data |
| **Animations on every data point for streaming (<5s intervals)** | Jittery, distracting, wastes CPU. Users can't follow 400ms easings 12+ times per minute | Disable animations for high-frequency updates; use `chart.update('none')` |

### Dependencies

```
In-place data updates ← Prevents chart destroy+recreate ← Resolves flicker
Smooth new-point transition ← Requires in-place updates
Crosshair tooltip ← Already partially working (enhance mode)
Pulse effect ← Works independently
Animated gradient ← Requires chart instance stability
Threshold zones ← Works independently  
Decimation ← Needs large dataset to matter
Timespan selector ← Needs backend API change
```

---

## 2. Gauge/Dial Visualizations (DASH-02)

### What to Gauge

Research across weather dashboard projects (WeatherNode, WeeWX AganetWX, Tempest Dashboard, ESP32 stations, WeatherDial) shows consistent patterns:

| Metric | Gauge Form | Common Scale | Color Zones |
|--------|-----------|-------------|-------------|
| **Temperature** | 180° half-doughnut | -10 to 50°C | Blue(<-5) → Green(10-25) → Orange(30-35) → Red(>35) |
| **Humidity** | 180° half-doughnut | 0-100% | Red(<30) → Green(40-60) → Blue(>80) |
| **Wind Speed** | 270°-300° arc (traditional dial) | 0-30 m/s (or Beaufort) | Green(<5) → Yellow(10-15) → Red(>20) |
| **Wind Direction** | 360° compass (existing SVG works) | 0-360° | N/A (circular) |
| **Rain Rate** | 180° half-doughnut | 0-50 mm/h | Green(<2) → Yellow(10) → Red(>25) |
| **Barometric Pressure** | 270° dial | 960-1060 hPa | Red(<990) → Green(1013) → Blue(>1030) — wait, that's inverted? |

Note: wxstat doesn't currently expose barometric pressure from the WHx080 sensor. Rain gauge could be "rain rate" if sensor provides it, or "rain today" which works as a bounded value (0-20mm typical).

### Table Stakes

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Animated needle** — needle transitions smoothly between old and new value on update | Static needles feel broken; the whole point of a gauge is the motion | **Low** | [chartjs-gauge (sourcentis)](https://github.com/sourcentis/chartjs-gauge) provides this natively. Uses Chart.js update cycle with `animation.duration` |
| **Colored segment zones** — green/yellow/red arcs indicating safe/warning/danger ranges | Users need instant visual context without reading scale numbers | **Low** | Built into chartjs-gauge via `data: [33, 66, 100]` boundaries + `backgroundColor` array |
| **Value label** — current value displayed numerically inside the gauge | Needle alone is ambiguous; numeric readout provides precision | **Low** | chartjs-gauge `valueLabel` config — formatter, color, background, padding |
| **Responsive gauge sizing** — gauge shrinks/grows with container | Fixed-size gauges break responsive grid layouts | **Low** | Uses Chart.js `responsive: true` standard |
| **Configurable scale range** — `minValue` / `maxValue` per gauge | Different metrics need different ranges (temp -10..50, humidity 0..100) | **Low** | chartjs-gauge `data` array defines boundaries; `minValue` offsets start |

### Differentiators

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Dual-scale labels** — gauge shows both °C and a subjective label (Cold/Mild/Warm/Hot) on the arc | Adds interpretive value — users see "Warm" on the gauge face without doing mental conversion | **Medium** | Requires custom `afterDraw` or Chart.js plugin to add text labels along the arc. Not in chartjs-gauge out of box |
| **Trend indicator** — small arrow or band showing whether value is rising, falling, or steady (Δ from last period) | Critical for pressure (approaching storm) but useful for all metrics. Already partially present in weather station UX | **Low** | Simple CSS arrow (⬆/⬇/➡) displayed below the gauge. Compare current value to previous reading |
| **Gauge header animation** — metric icon or title subtly animates when value crosses a threshold (e.g., temp icon pulses red when >35°C) | Creates urgency without requiring constant gauge watching | **Low** | CSS animation toggled by class. Already have emoji icons in current code |
| **Dual units** — gauge shows both m/s and Beaufort on wind gauge, or °C and °F on temp | Caters to users who think in multiple systems. Common in professional weather dashboards | **Medium** | Requires `afterDraw` plugin to render a secondary scale ring. Or show primary on gauge, secondary in a subtitle |
| **Dark/light theme aware gauge colors** — segment colors adjust for readability in both themes | The existing dashboard is dark-only, but future-proofing matters | **Low** | Use CSS custom properties for gauge colors; chartjs-gauge accepts string colors that can reference CSS vars indirectly via re-init |
| **Full-circle wind direction gauge** — 360° gauge with compass points (N/S/E/W) labeled | More intuitive than the existing small SVG compass for wind direction | **Medium** | chartjs-gauge supports `circumference: 360` + `rotation: -180`. Need compass point labels drawn via `afterDraw`. The existing SVG compass is already well-done though — gauge is a complement, not replacement |

### Anti-Features

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Gauge for every possible metric** (barometric pressure, UV, solar radiation, lightning, etc.) | Clutters dashboard; not all are useful as gauges. WHx080 may not expose all these values anyway | Gauge only: temperature, humidity, wind speed, and optionally rain. Keep rest as numeric cards |
| **Over-styled "glass" gauges with 3D bevels** | Looks dated, fights the cyberpunk dark theme, accessibility issues | Flat neon-accented arcs consistent with existing theme (cyan/magenta/green) |
| **Clock-style 360° gauge for temperature** | 360° temp gauge wastes space; 180° half-doughnut is the standard for bounded metrics | Use 180° for humidity, temperature, rain; 270° for wind speed (traditional dial aesthetic) |
| **Analog gauges that don't update when data is stale** | Shows misleading current value | Grey out the gauge or fade it when data age exceeds threshold (>300s as in existing code) |
| **Gauges replacing existing metric cards entirely** | The existing cards are clean and informative. Gauges complement them, adding visual interest | Replace cards with gauges for temp, humidity, wind. Keep existing card layout for dew point and rain (or rain gauge if rate data available) |

### Dependencies

```
chartjs-gauge library install ← Need to add via CDN or bundled script
Gauges replacing some metric cards ← Layout change (cards → gauges section)
Animated needle ← Provided by chartjs-gauge, just configure
Trend indicator ← Needs current vs previous value comparison (already fetched)
Dual units ← Optional, afterDraw complexity
```

---

## 3. Current Conditions Panel (DASH-03)

### Current State

Currently the dashboard has a 5-card grid (temp, humidity, wind+compass, dew point, rain) with a separate status bar. This *is* a current conditions panel, but it's not visually prominent or grouped as a single "hero" section. The status info (live/stale, last seen age, station ID) sits in a separate header bar.

### What a Current Conditions Panel Should Be

Research from tempest-dashboard, WeatherNode, WeeWX AganetWX, nimbus-weather-card, and radar-wise shows consistent patterns:

**Layout:** F-pattern → temperature in top-left (the single most important metric), then adjacent supporting metrics, then secondary details below.

**Content hierarchy:**
1. **Primary** (largest, most prominent): Temperature + condition icon + feels-like
2. **Secondary** (medium prominence): Humidity, wind speed+direction, dew point, rain
3. **Tertiary** (smaller, supporting): Station health, timestamps, daily range

### Table Stakes

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Temperature as hero element** — largest, boldest, top-left | Temperature is the #1 question users ask. F-pattern eye tracking puts it top-left | **Low** | Already exists but not visually differentiated enough. The current layout gives all cards equal visual weight |
| **"Feels like" / apparent temperature** — displayed immediately below the main temperature | User's second question after "what's the temp?" is "what does it actually feel like?" | **Low** | Currently only shown as a simple text label ("Warm", "Cold", etc.). Should be a proper "Feels like 22°C" value |
| **Data freshness indicator per-card** — each metric shows when it was last updated | Sensors can fail independently. The current single status dot doesn't tell you *which* reading is stale | **Medium** | Requires adding timestamp to individual sensor readings in the API, or using a per-metric age heuristic |
| **Today's high/low range** — min/max for each metric with time of occurrence | Users want to know "how does now compare to the rest of today" | **Low** | `/api/stats` already returns `t_min`, `t_max`, `h_min`, `h_max`, `w_max`. Currently displayed in card `.range` div. Improve formatting with times |
| **Aggregated daily rain total + rain rate** — total rain today + current rate | Rain is the most context-dependent metric. Total matters, but so does rate (is it raining *now*?) | **Low** | `/api/current` returns `rain_today_mm`. Rain rate would need additional sensor processing or a cumulative-change-per-interval calculation |
| **Consistent missing/loading state** — -- or N/A placeholders when sensor data is absent | Sensor dropout is normal (RF interference, battery). Dashboard must not break | **Low** | Already handled in current code (ternary `!= null ? ... : '--'`). Maintain this pattern |

### Differentiators

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Dynamic color tinting** — the hero temperature area subtly shifts hue based on temperature range (ice-blue when freezing → amber when warm → deep red when hot) | Instant emotional read — you feel the temperature before reading the number. Used in WeeWX AganetWX | **Low** | CSS `box-shadow` or `background` transition based on temperature value. Already have colored accent per card; make temperature hero's background/glow dynamic |
| **Weather mood / condition caption** — a one-word atmospheric caption under the feels-like: "Brrr!", "Perfect!", "Melting!" | Adds personality. WeeWX AganetWX does this. The existing code already has a less-visible version in `tempIcon()` emoji and `feels` text | **Low** | Pure JS — map temperature+humidity to a curated word list. Enhance the existing `feels` text |
| **Trend arrows (⬆ ⬇ ➡) on each metric** — small arrow showing direction of change vs previous reading | Answers "is it getting hotter/colder/wetter?" at a glance. Common on professional weather dashboards | **Low** | Compare current value to previous `/api/current` response. Store previous values in JS. Render arrow + optional delta |
| **Dew point comfort zone** — colored comfort indicator for dew point (dry/comfortable/humid/muggy) | Dew point is more meaningful than relative humidity for human comfort. Already partially implemented | **Low** | Current code has a basic version. Expand with color-coded badge + classification |
| **Station health footer** — compact row showing: last sample age, battery (if available), signal strength, WiFi RSSI | Operational awareness for the admin/user who hosts the station | **Medium** | WHx080 doesn't report battery via rtl_433 decoding natively. Age and connectivity are available. Battery would need to be a future enhancement or checked on the sensor |
| **Expanded wind detail** — wind speed + gust speed + Beaufort scale + direction string all visible together | Wind is the most information-dense metric. Users want all these without clicking | **Low** | Current layout already shows most of this. Group into a single visual block with better hierarchy |
| **Pressure trend indicator** — small bar showing pressure change over 3h/6h (rising/falling/steady) | Classically important for predicting weather changes. "Rapidly falling" signals approaching storm | **Medium** | WHx080 may or may not expose barometric pressure. If available, show trend with NWS-style wording ("Rising rapidly", "Falling slowly", "Steady") |
| **Responsive layout reorganization** — on mobile, hero stacks vertically; on desktop, hero spans full width with supporting metrics in a 2-4 column grid | Critical for the dashboard to work on phones and wall-mounted tablets | **Medium** | Current CSS already has responsive breakpoints. Hero section needs specific mobile treatment (larger fonts, single column, no horizontal scroll) |

### Anti-Features

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Map integration** (weather radar map) | Out of scope for current milestone. Adds heavy dependencies, no clear data source for WHx080 local station | Keep as future milestone if desired. Focus on sensor data, not forecast |
| **Forecast display** (7-day, 14-day) | wxstat is a local sensor dashboard, not a forecast app. No API integration planned | Current conditions panel should show what the sensor reads, not forecasts |
| **Cluttered hero** — cramming all metrics into a single large card with tiny text | Defeats the purpose. A "hero" should be sparse and focus on the 1-3 most important readings | Temperature + feels-like + condition icon as hero. Humidity, wind, rain as adjacent supporting cards |
| **Over-writing existing working layout** | The current 5-card grid works and users are familiar with it. Gradual enhancement | Add a dedicated hero section above the existing cards. The hero replaces the first row (temp becomes hero). Existing cards can shift down or merge into gauges |
| **Automatic geolocation / city search** | Not relevant for a fixed personal weather station. The station doesn't move | Hard-code or use hostname (already done) |
| **Animated weather effects** (rain falling on screen, snow, fog overlays) | While flashy, they consume CPU, are distracting over time, and don't add data insight | Keep the static ambient grid background and scanline overlay that already exists |

### Dependencies

```
Hero temperature section ← New layout element above existing cards
Dynamic color tinting ← Depends on hero section existing
Feels-like as proper value ← Enhance existing code
Trend arrows ← Store previous values per-metric in JS
Today's high/low ← Already have /api/stats data, improve display
Station health ← Needs more sensor metadata (battery, uptime)
Pressure trend ← Only if pressure data is available from WHx080
Responsive hero layout ← CSS grid work
```

---

## Feature Dependency Map

```
Phase ordering dependencies between these three feature groups:

├── Animated Chart Interactions ──────────────────────
│   └── In-place data updates (prerequisite for all other chart improvements)
│       ├── Smooth new-point transitions
│       ├── Animation cadence control
│       └── Stable Y-axis
│
├── Gauge/Dial Visualizations ────────────────────────
│   └── Install chartjs-gauge (no code deps on chart improvements, but benefits from them)
│       ├── Temperature gauge (replaces temp card)
│       ├── Humidity gauge (replaces hum card)
│       ├── Wind speed gauge (complements wind card)
│       └── Rain gauge (if rate data available)
│
└── Current Conditions Panel ─────────────────────────
    ├── Hero temperature section (new top-level element)
    ├── Dynamic color tinting (pure CSS/JS, no backend change)
    ├── Trend arrows (store previous values client-side)
    ├── Today's high/low (already have API data)
    └── Station health (backends: needs new API data)
```

---

## MVP Recommendation (for this milestone)

**Phase 1 — Chart Stability + Current Conditions:**
1. Fix chart destroy+recreate → in-place data update (highest bang-for-buck UX fix)
2. Add smooth new-point transitions (fromX/fromY callbacks)
3. Build hero temperature section with dynamic color tinting + feels-like
4. Add trend arrows to all metrics
5. Enhance today's high/low display

**Phase 2 — Gauges:**
6. Install chartjs-gauge (CDN or bundled)
7. Replace temp and humidity cards with 180° gauges
8. Add wind speed gauge (270° dial) alongside existing compass
9. Add optional rain gauge

**Defer:**
- Timespan selector (needs backend API change)
- Dual units on gauges (nice-to-have, adds complexity)
- Station health footer (needs new data from sensor)
- Pressure trend (only if sensor supports it)
- Full-circle wind gauge (existing SVG compass works well)

---

## Sources

| Source | URL | Confidence | Used For |
|--------|-----|------------|----------|
| Chart.js Animation Docs | https://www.chartjs.org/docs/latest/configuration/animations.html | HIGH | Chart animation patterns |
| sourcentis/chartjs-gauge | https://github.com/sourcentis/chartjs-gauge | HIGH | Gauge plugin API, configuration | 
| Chart.js Decimation Plugin | https://www.chartjs.org/docs/latest/configuration/decimation.html | HIGH | Large dataset performance |
| Digital Urban MQTT Dashboard | https://www.digitalurban.org/blog/2024/06/27/enhancing-live-weather-monitoring-with-mqtt-and-chart-js/ | MEDIUM | Pulse effect, live update patterns |
| TheLinuxCode Chart.js Dynamic Updates | https://thelinuxcode.com/how-to-dynamically-update-values-of-a-chart-in-chartjs/ | MEDIUM | Streaming update patterns, anti-patterns |
| SO: Chart.js line dropping on new value | https://stackoverflow.com/questions/79134989/how-to-avoid-chartjs-line-coming-down-when-adding-new-value | HIGH | fromX/fromY callback solution |
| WeeWX AganetWX Skin | https://github.com/aganet/weewx-aganetwx | HIGH | Hero section design, dynamic color tinting, gauge practices |
| WeatherNode Dashboard | https://www.weathernode.dev/ | MEDIUM | Dashboard layout patterns, sensor health |
| Tempest Dashboard | https://github.com/gibbonsr4/tempest-dashboard | MEDIUM | Wind gauge, live WebSocket patterns |
| ESP32 Weather Station Dashboard | https://github.com/cerevisis/ESP32-Weather-Station | MEDIUM | Gauge chart usage, responsive layout |
| RadarWise HA Card | https://github.com/TheWillMiller/radar-wise | MEDIUM | Current conditions panel layout patterns |
| Nimbus Weather Card | https://github.com/alpha520098/nimbus-weather-card | MEDIUM | Card layout patterns, local sensors display |
| Dashboard Design Best Practices | https://uxpilot.ai/blogs/dashboard-design-principles | HIGH | F-pattern layout, KPI hierarchy, anti-patterns |
| JustInMind Dashboard Design | https://www.justinmind.com/ui-design/dashboard-design-best-practices-ux | HIGH | One-screen principle, card grouping |
| Pencil & Paper Dashboard UX | https://www.pencilandpaper.io/articles/ux-pattern-analysis-data-dashboards | HIGH | Progressive disclosure, consistent card layout |

---

## Confidence Assessment

| Area | Level | Reason |
|------|-------|--------|
| Chart animation patterns | **HIGH** | Official Chart.js docs + multiple verified implementations |
| Gauge library capabilities | **HIGH** | chartjs-gauge source is clear, examples work with Chart.js v4 |
| Layout / UX patterns | **HIGH** | Consistent across 10+ weather dashboard projects and UX research |
| Feature complexity estimates | **MEDIUM** | Based on code inspection and known patterns; actual effort depends on inline HTML complexity |
| WHx080 sensor capabilities | **MEDIUM** | Not all features confirmed (pressure, battery status) — needs sensor datasheet verification |
