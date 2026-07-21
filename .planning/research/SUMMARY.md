# Project Research Summary

**Project:** wxstat — Fine Offset WHx080 Dashboard
**Domain:** Self-hosted weather station web dashboard (visualization enhancements)
**Researched:** 2026-07-21
**Confidence:** HIGH

## Executive Summary

Wxstat is a single-page weather dashboard serving live sensor data via a Flask backend with Chart.js line charts, metric cards, and a cyberpunk dark theme. The three feature areas researched (animated chart transitions, analog gauge widgets, a current conditions hero panel) transform a functional but static dashboard into a polished, glanceable weather station interface. The research reveals a clear dependency chain: the most impactful single fix is replacing Chart.js destroy+recreate with in-place data updates, which stops visible flicker and enables smooth animation on the existing 60-second poll cycle.

**Recommended approach:** A four-phase build. Phase 1 establishes a clean JavaScript module structure and real-time data pipeline (SSE), fixing the destroy+recreate anti-pattern and paving the way for all visual enhancements. Phase 2 adds analog gauge widgets via `@sourcentis/chartjs-gauge` (a 6 KB CDN plugin that shares Chart.js's rendering pipeline). Phase 3 builds a prominent current-conditions hero panel with dynamic color tinting and trend indicators. Phase 4 polishes all animations into a cohesive, performance-optimized experience.

**Key risks:** The most significant pitfalls are (1) cumulative data growth without eviction — capping frontend data to 288 points per series is essential before adding animations; (2) poll+animation render contention — data ingestion must be decoupled from visual commit via a buffer-and-rAF pattern; and (3) information overload — the hero panel must show ≤7 metrics with clear hierarchy. A moderate risk is the `@sourcentis/chartjs-gauge` library being new (v1.2.0, 0 stars), but its small codebase and grounded-in-Chart.js-API design make it recoverable.

## Key Findings

### Recommended Stack

The stack recommendation is conservative: zero new build tools, one new CDN dependency (6 KB gzipped). Everything loads via `<script>` tags, keeping the `python3 wx_server.py` deployment contract intact.

**Core technologies:**
- **Chart.js 4.x** (already loaded): Keep. Enhance animation config with `easeOutCubic` easing, 600ms duration, and a separate 300ms `reset` transition for streaming updates. No upgrade needed.
- **@sourcentis/chartjs-gauge 1.2.0** (new): Chart.js plugin that registers `type: 'gauge'` on the doughnut controller. Provides animated needle, colored segment zones, value label, and responsive sizing — everything DASH-02 needs.
- **CSS transitions** (no library): For value animation in the current conditions panel. Weather values change slowly (1-2°C per refresh), so CSS `transition` on `opacity`/`transform` is sufficient. No countUp.js or anime.js needed.

**CDN dependency (only new one):**
```html
<script src="https://cdn.jsdelivr.net/npm/@sourcentis/chartjs-gauge@1.2.0/dist/chartjs-gauge.umd.js"></script>
```

**Alternatives rejected:** D3.js (overkill), JustGage (visual inconsistency with Chart.js canvas), canvas-gauges (abandoned 2020), custom afterDraw needle (reinventing a solved problem).

### Expected Features

Three feature groups with well-defined dependencies:

**DASH-01 — Animated Chart Interactions (foundation):**
- *Must have:* In-place data updates (stop destroy+recreate), smooth new-point transitions via `fromX`/`fromY` callbacks, crosshair tooltip (`mode: 'nearest'`, `intersect: false`), animation cadence aligned to poll frequency, stable Y-axis with `suggestedMin`/`suggestedMax`
- *Differentiators:* Pulse effect on new data (CSS class toggle), segmented threshold zones (afterDraw plugin), decimation for large datasets
- *Anti-features:* Chart destroy+recreate on every tick (current code — flickers), 3D effects, pie charts, full DOM rebuild

**DASH-02 — Gauge/Dial Widgets (complement to line charts):**
- *Must have:* Animated needle, colored segment zones (green/yellow/red), numeric value label, responsive sizing, configurable scale range
- *Differentiators:* Trend indicators (⬆⬇➡ below gauge), gauge header animation on threshold cross, dual units (optional)
- *Anti-features:* Gauges for every possible metric (clutters), 3D glass bevel look, 360° gauges for bounded metrics, replacing metric cards entirely
- *Metrics to gauge:* Temperature (180°, -20 to 50°C), Humidity (180°, 0-100%), Wind Speed (270°, 0-30 m/s), optionally Rain

**DASH-03 — Current Conditions Panel (hero section):**
- *Must have:* Temperature as hero element (largest, top-left), feels-like display, data freshness indicator per-card, today's high/low range, aggregated rain total + rate, consistent missing/loading states
- *Differentiators:* Dynamic color tinting (hero background shifts hue with temp), weather mood caption, trend arrows on all metrics, dew point comfort zone, responsive layout reorganization
- *Anti-features:* Map integration, forecast display, cluttered hero (>7 metrics), overwriting existing working layout, animated weather effects

**Defer to post-MVP:**
- Timespan selector (6h/24h/7d) — needs backend API change
- Dual units on gauges — optional afterDraw complexity
- Station health footer — needs new sensor metadata
- Pressure trend — only if WHx080 supports it
- Full-circle wind gauge — existing SVG compass works well

### Architecture Approach

The architecture evolves from a monolithic inline-HTML Flask template to a modular client-side application while preserving single-file deployment. The key structural change is extracting JavaScript from `_DASHBOARD_HTML` into a separate `_DASHBOARD_JS` constant served via `/dashboard.js`, keeping code organized without adding a build step.

**Major components:**
1. **SSE Queue** (new) — `queue.Queue(maxsize=50)` in the collector thread, decouples data production from consumption. After SQLite insert, pushes reading to queue; SSE endpoint blocks on `queue.get(timeout=30)` with heartbeat keepalives.
2. **SSE Endpoint** `/api/stream` (new) — long-lived HTTP connection emitting new readings as Server-Sent Events. Uses Flask streaming response. Auto-reconnect via browser `EventSource`. Polling kept as fallback on connection loss.
3. **JS Data Layer** — Central `state` object with `historyRows[]`, chart registry `charts{}`, and `handleNewReading()` as the single mutation point. Views (gauges, line charts, conditions panel) are pure renderers of state.
4. **Gauge Widgets** (new) — chartjs-gauge instances for temperature, humidity, wind speed, optionally rain. Each gets its own `<canvas>` element, independently sized and positioned.
5. **Current Conditions Panel** (new) — Hero section compositing existing `/api/current` data. Large temperature + condition icon + feels-like + trend arrows. Zero new API endpoints.
6. **Line Charts** (enhanced) — Existing 4 Chart.js line charts, modified to use `appendChartPoint()` instead of destroy+recreate. Animation config enhanced with better easing.

**Data flow:** Page load → HTTP fetch `/api/history` (backfill) + `/api/stream` (SSE). Steady-state: collector → SQLite → SSE queue → SSE endpoint → EventSource.onmessage → handleNewReading() → update all views. Single mutation point prevents race conditions.

### Critical Pitfalls

1. **Full-chart redraw every frame kills performance:** The current destroy+recreate pattern causes visible flicker. Even `chart.update()` on every SSE event forces full dataset re-processing. *Fix: Use `chart.update('none')` for streaming, decimate data to ≤1000 pts, disable animations on history charts, reserve animation for gauges.*

2. **Cumulative data growth without eviction:** Without frontend data capping, Chart.js carries every reading in memory. After a month: ~43,000 points per series. *Fix: Cap frontend data at 24h window (288 pts), add backend `?hours=` and `?aggregate=` parameters for history filtering.*

3. **Poll + animation render contention:** `setInterval` fetching + `requestAnimationFrame` gauge animation creates race conditions where mid-animation `chart.update()` interrupts the needle sweep. *Fix: Decouple data ingestion from visual commit. Use buffer-and-rAF pattern. Never call `chart.update()` from fetch callback directly.*

4. **Information overload — seeing everything equally:** Adding hero panel + 4 gauges + 4 line charts creates visual noise. *Fix: Establish clear hierarchy (hero > gauges > history charts). Show ≤7 metrics in hero. Group related metrics. Use size, position, and color saturation for weight. Demote static values.*

5. **Chart.js animation defaults for weather data:** Default `tension`, `pointRadius`, and animation mode are designed for business charts, not weather time-series. *Fix: Set `pointRadius: 0`, `tension: 0` (straight lines are more honest), `animation: false` on history charts. Only animate gauge needles and initial chart mount.*

## Implications for Roadmap

Based on the research, the recommended phase structure follows the dependency graph established in ARCHITECTURE.md, cross-referenced with the feature priority from FEATURES.md and pitfall prevention from PITFALLS.md.

### Phase 1: SSE Data Pipeline + JS Refactoring

**Rationale:** Foundation for everything else. The destroy+recreate fix alone delivers the biggest UX improvement (no flicker). Extracting JS and adding SSE creates a clean data flow that prevents architecture-level rework in later phases. Doing this first ensures Phases 2-3 can simply add renderers to the existing data layer.

**Delivers:**
- Chart destroy+recreate replaced with in-place data mutations
- JS extracted into `_DASHBOARD_JS` constant, served via `/dashboard.js`
- JS refactored into IIFE module with centralized `state` and chart registry
- SSE endpoint `/api/stream` with bounded queue
- `EventSource` consumer in browser with polling fallback
- Initial animation config enhancement (duration, easing)

**Addresses from FEATURES.md:** In-place data updates, smooth new-point transitions (partial), animation cadence, stable Y-axis
**Avoids from PITFALLS.md:** Full-chart redraw every frame (P1), Poll+animation contention (P5) — by establishing the buffer-and-rAF pattern
**Stack:** Chart.js 4.x (existing, enhanced config only)

### Phase 2: Analog Gauge Widgets

**Rationale:** Depends on Phase 1's data flow (SSE delivers live readings to `handleNewReading()`, which can then call `updateAllGauges()`). Gauges can be built and tested independently once the data pipeline is in place. Each gauge is a self-contained Chart.js instance.

**Delivers:**
- Temperature gauge (180° half-doughnut, -20 to 50°C, 4 colored zones)
- Humidity gauge (180°, 0-100%, 3 zones)
- Wind speed gauge (270° dial, 0-30 m/s, 4 zones)
- Optionally rain gauge (180°, 0-50mm)
- Animated needle on every SSE event
- Numeric value label inside each gauge
- `createGauge()` / `updateAllGauges()` functions

**Addresses from FEATURES.md:** Gauge table stakes (needle, segments, label, responsive), trend indicators beneath gauges
**Avoids from PITFALLS.md:** Doughnut-hack gauges break at small sizes (P3) — uses purpose-built plugin, but test at 100/75/50px widths; Pretty but unreadable gauges (P6) — ensure numeric label is prominent
**Stack:** Add `@sourcentis/chartjs-gauge` from CDN (6 KB gzipped)

### Phase 3: Current Conditions Panel

**Rationale:** Pure UI work. All data already flows through Phase 1's data layer. The conditions panel is a new view function (`updateConditionsPanel()`) that reads condition/icon/description fields already computed server-side. No new API endpoints needed.

**Delivers:**
- Hero section: large condition icon + temperature + feels-like
- Dynamic color tinting (hero background hue shifts with temperature)
- Trend arrows (⬆⬇➡) on all metrics from stored previous values
- Enhanced today's high/low display with timestamps
- Comprehensive dew point comfort zone display
- Responsive layout: F-pattern on desktop, stacked on mobile
- Data freshness indicator per metric

**Addresses from FEATURES.md:** All DASH-03 must-haves, dynamic color tinting, trend arrows, dew point comfort zone, responsive layout
**Avoids from PITFALLS.md:** Information overload (P4) — show ≤7 primary metrics; Conditions panel as data dump (P7) — editorial curation required; Gauge ranges mismatch (implicit) — but this phase doesn't set ranges, that's Phase 2
**Stack:** CSS transitions only (no new JS library)

### Phase 4: Animation Polish

**Rationale:** All components exist. This phase refines visual feel without structural changes. Can be done iteratively or as a focused polish pass.

**Delivers:**
- Enhanced chart animations (staggered series entrance, smoother easing)
- Gauge needle transition smoothing (tweak timing curve)
- Current conditions panel entrance/update animation (subtle fade)
- Pulse effect on new data arrival (CSS class toggle)
- Tab visibility pause via `IntersectionObserver`
- Perf tuning: verify frame budget <16ms after 1h continuous operation

**Addresses from FEATURES.md:** Pulse effect, animated gradient fill (optional), animation refinement for all components
**Avoids from PITFALLS.md:** Full-chart redraw performance (P1 — final verification), Animation defaults for weather data (P8 — override tension, pointRadius, animation)
**Stack:** Chart.js animation config + CSS transitions (existing)

### Phase Ordering Rationale

- **Phase 1 is non-negotiable first** — the destroy+recreate anti-pattern blocks meaningful animation work. Without a clean data pipeline, gauges (Phase 2) would need their own fetch logic, and the conditions panel (Phase 3) would read from `window` globals. Architecture research is unanimous on this dependency.
- **Phases 2 and 3 are independent after Phase 1** — gauges and the conditions panel can be built in either order (or in parallel). Gauges are recommended first because they benefit more from the CDN dependency and need the SSE data flow at full resolution. The conditions panel works fine with the 60s poll cycle if SSE isn't available yet.
- **Phase 4 is last because visual polish on a moving target is wasteful** — if the hero panel layout changes during Phase 3, the Phase 4 entrance animation would need rework. Let all components stabilize first.
- **Every phase produces a shippable dashboard** — after Phase 1, the dashboard is identical visually but no longer flickers. After Phase 2, gauges enhance the visual experience. After Phase 3, the hero panel completes the glanceable design. Phase 4 is the polish pass.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 1 (SSE + JS Refactor):** The SSE queue implementation needs confirmation that Flask's threaded mode handles concurrent SSE connections without blocking. Architecture recommends `queue.Queue` but the collector thread architecture (rtl_433 subprocess vs. threading) needs verification against the existing codebase. Flag: `/gsd-plan-phase --research-phase 1`
- **Phase 2 (Gauges):** The `@sourcentis/chartjs-gauge` library is low-traffic (v1.2.0, 0 stars). While its codebase is small and recoverable, a brief spike test is recommended before committing: load it in the dashboard, create one gauge, verify animated needle on `chart.update()`. Flag: `/gsd-plan-phase --research-phase 2`

Phases with well-documented patterns (skip research-phase):
- **Phase 3 (Conditions Panel):** Pure CSS/HTML layout work. No new dependencies, no API changes. Follow established F-pattern layout and the existing dashboard's CSS variable system. Known patterns.
- **Phase 4 (Animation Polish):** Chart.js animation docs are thorough. CSS transitions are well-documented. Tab visibility via `IntersectionObserver`/`document.hidden` is standard. No research needed.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Chart.js 4.x is already loaded and verified. `@sourcentis/chartjs-gauge` API is documented. CDN URLs verified. Alternatives analyzed with clear rationale. |
| Features | HIGH | Consistent across 10+ weather dashboard projects and UX research sources. Feature complexity estimates based on code inspection of existing dashboard. Feature dependency map is well-reasoned. |
| Architecture | HIGH | SSE queue pattern is battle-tested in Flask apps. Chart.js data mutation pattern is official. Module structure is standard IIFE approach. Each phase produces a verifiable intermediate state. |
| Pitfalls | HIGH | 8 critical pitfalls sourced from Chart.js performance docs (official), community bug reports, dashboard UX research, and weather-specific HMI literature. Recovery strategies are practical. |

**Overall confidence:** HIGH

### Gaps to Address

- **WHx080 sensor capabilities:** Pressure data availability from the Fine Offset WHx080 is unconfirmed. If pressure is available, a pressure trend gauge/indicator could be added to Phase 3. If not, no change needed. *Handle: During Phase 1, check the raw rtl_433 output for any pressure/barometer fields.*
- **Battery/signal status:** The sensor may report battery level or signal strength. If available, station health could be added to Phase 3's hero panel footer. *Handle: Investigate during Phase 1 implementation; add if data exists.*
- **chartjs-gauge Retina/4K behavior:** The arch research notes chartjs-gauge uses Chart.js canvas pipeline which handles devicePixelRatio, but actual Retina rendering needs visual verification. *Handle: Test during Phase 2 spike before committing the library.*
- **Polling fallback transition:** The SSE-to-polling fallback logic needs careful edge-case handling (reconnection storms, duplicate data dedup). The architecture spec covers dedup via `reading.id > lastId`, but recovery after prolonged disconnect isn't detailed. *Handle: Define in Phase 1 plan.*

## Sources

### Primary (HIGH confidence)
- [Chart.js Animation Documentation](https://www.chartjs.org/docs/latest/configuration/animations.html) — Easing functions, transition modes, callback API
- [Chart.js Performance Guide](https://www.chartjs.org/docs/latest/general/performance.html) — Streaming patterns, decimation, animation tradeoffs
- [Chart.js Issue #10073](https://github.com/chartjs/Chart.js/issues/10073) — Performance regression 3.x vs 2.x (community, 200+ comments)
- [@sourcentis/chartjs-gauge GitHub](https://github.com/sourcentis/chartjs-gauge) — Full README, compatibility table, CDN usage, examples
- [MDN: EventSource API](https://developer.mozilla.org/en-US/docs/Web/API/EventSource) — Browser SSE consumption, auto-reconnect behavior
- [Flask SSE without dependencies](https://maxhalford.github.io/blog/flask-sse-no-deps/) — Queue-based SSE pattern for threaded Flask
- [WeeWX AganetWX Skin](https://github.com/aganet/weewx-aganetwx) — Hero section design, dynamic color tinting, gauge practices
- [Dashboard Design Best Practices](https://uxpilot.ai/blogs/dashboard-design-principles) — F-pattern layout, KPI hierarchy, anti-patterns
- [DOMO: Top 10 Dashboard Design Mistakes](https://www.domo.com/) — Dashboard UX research, widget limits
- [HMI Gauge Minimum Sizes](https://industrialmonitordirect.com/) — Gauge readability thresholds, SVG approach

### Secondary (MEDIUM confidence)
- [SO: Chart.js new-value animation](https://stackoverflow.com/questions/79134989/) — fromX/fromY callback pattern for streaming
- [Digital Urban MQTT Dashboard](https://www.digitalurban.org/blog/2024/06/27/enhancing-live-weather-monitoring-with-mqtt-and-chart-js/) — Pulse effect, live update patterns
- [TheLinuxCode Chart.js Updates](https://thelinuxcode.com/how-to-dynamically-update-values-of-a-chart-in-chartjs/) — Streaming update patterns, anti-patterns
- [chartjs-gauge npm packages](https://www.npmjs.com/) — Community gauge implementations, version ranges
- [JustGage v2](https://toorshia.github.io/justgage/) — Alternative gauge library (documented as rejected alternative)
- [Vesper Sky: Weather UX](https://www.vespersky.com/) — Weather dashboard glanceability research

### Tertiary (LOW confidence)
- WeatherNode, Tempest Dashboard, ESP32 stations — Medium confidence layout patterns; used for pattern corroboration, not primary technical decisions
- WHx080 sensor datasheet — Not directly accessed; sensor capabilities inferred from existing codebase + rtl_433 output

---

*Research completed: 2026-07-21*
*Ready for roadmap: yes*
