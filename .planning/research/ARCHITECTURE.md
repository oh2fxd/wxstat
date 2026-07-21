# Architecture Research: Animated Charts, Analog Gauges & Current Conditions

**Domain:** Weather station dashboard (Flask + Chart.js single-page app)
**Researched:** 2026-07-21
**Confidence:** HIGH

## Executive Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Gauge library | `chartjs-gauge` (Chart.js plugin) | No new major dependency; same rendering pipeline; 4.77 KB extra from CDN |
| JS structure | Separate template constant, served via Flask route | Maintainability without breaking single-file deployment |
| Real-time update | SSE (`/api/stream`) + in-memory queue, polling fallback | Low latency, no WebSocket complexity, auto-reconnect via `EventSource` |
| Current conditions panel | Client-side compositing of existing `/api/current` + `/api/stream` | No new API endpoints needed; pure UI work |

---

## Component Architecture

```
                          ┌──────────────────────────────────────┐
                          │           Browser (Dashboard)         │
                          │                                      │
                          │  ┌─────────┐  ┌───────────────────┐  │
                          │  │ Current │  │  Gauge Widgets     │  │
                          │  │Conditions│  │  (chartjs-gauge)   │  │
                          │  │ Panel   │  │  ┌───┐ ┌───┐ ┌──┐ │  │
                          │  │ (hero)  │  │  │ T │ │ H │ │ W │ │  │
                          │  └────┬────┘  │  │emp│ │um │ │ind│ │  │
                          │       │       │  └───┘ └───┘ └──┘ │  │
                          │       │       └───────────────────┘  │
                          │       │                              │
                          │  ┌────▼───────────────────────────┐  │
                          │  │  Line Charts (Chart.js)         │  │
                          │  │  temp · humidity · wind · rain  │  │
                          │  └────────────────────────────────┘  │
                          │                                      │
                          │  ┌────────────────────────────────┐  │
                          │  │  Data Layer (JS)               │  │
                          │  │  ┌──────────┐ ┌─────────────┐  │  │
                          │  │  │history[] │ │ SSE Handler  │  │  │
                          │  │  └──────────┘ │(EventSource) │  │  │
                          │  │               └──────┬───────┘  │  │
                          │  └──────────────────────┼──────────┘  │
                          └─────────────────────────┼─────────────┘
                                                    │ HTTP / SSE
                          ┌─────────────────────────┼─────────────┐
                          │  Flask (wx_server.py)    │             │
                          │                         ▼             │
                          │  ┌───────────────────────────────┐    │
                          │  │  REST API                      │    │
                          │  │  GET /api/current              │    │
                          │  │  GET /api/history?since=N      │    │
                          │  │  GET /api/stats                │    │
                          │  │  GET /api/stream    ◄── NEW    │    │
                          │  └──────────┬────────────────────┘    │
                          │             │                          │
                          │  ┌──────────▼────────────────────┐    │
                          │  │  SSE Queue (queue.Queue)      │    │
                          │  │  Collector→queue→SSE endpoint │    │
                          │  └───────────────────────────────┘    │
                          │             │                          │
                          │  ┌──────────▼────────────────────┐    │
                          │  │  rtl_433 Collector Thread     │    │
                          │  └───────────────────────────────┘    │
                          │             │                          │
                          │  ┌──────────▼────────────────────┐    │
                          │  │  SQLite (WAL)                 │    │
                          │  └───────────────────────────────┘    │
                          └──────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Why Separated |
|-----------|---------------|---------------|
| **SSE Queue** | Thread-safe message bus from collector to web clients | Decouples data production from consumption; avoids DB polling for live updates |
| **SSE Endpoint** (`/api/stream`) | Long-lived HTTP connection emitting new readings as SSE events | Enables real-time push without WebSocket; auto-reconnect is native in browser |
| **JS Data Layer** | Manages `history[]` array, routes data between SSE/HTTP and view components | Centralizes state mutations; views are pure renderers |
| **Gauge Widgets** | chartjs-gauge instances for T/H/W/R | Dedicated sub-component per metric; isolated configuration and animation |
| **Current Conditions Panel** | Prominent hero section combining latest reading + condition + icon | Pure layout concern; reads from existing data model |
| **Line Charts** | Existing Chart.js line charts for 24h history | Unchanged from current architecture; animation config enhanced |

---

## Data Flow

### Startup Sequence

```
1. Page Load
   ├── GET /api/history?limit=288   → historyRows[]
   ├── GET /api/current             → current reading snapshot
   └── GET /api/stream              → SSE connection (long-lived)
         │
         └── Initial backfill: collector sends latest reading via queue
              (avoids race between /api/current and first SSE event)
```

### Steady-State Flow

```
Collector Thread                    SSE Endpoint                    Browser
─────────────                       ────────────                    ───────
rtl_433 emits JSON
    │
    ▼
insert(SQLite)
    │
    ▼
queue.put(reading)  ──────────►  queue.get() (blocks)
                                      │
                                      ▼
                                 yield "data: {json}\n\n"  ────►  EventSource.onmessage
                                                                      │
                                                                      ▼
                                                                JS Data Layer
                                                                 ├── historyRows.push(reading)
                                                                 ├── trim to 288 max
                                                                 ├── update gauges (chart.update())
                                                                 ├── update line charts (chart.data追加)
                                                                 └── update Current Conditions panel (DOM)
```

### SSE Queue Architecture

```python
import queue

# In collector thread (_run_rtl433):
_sse_queue = queue.Queue(maxsize=50)  # bounded — don't accumulate if no clients

# After insert + analysis + rain computation:
_sse_queue.put(msg)

# SSE endpoint:
@app.route("/api/stream")
def api_stream():
    def event_stream():
        # 1. Send most recent reading as initial event
        latest = get_db().execute(
            "SELECT * FROM readings ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if latest:
            yield f"data: {json.dumps(dict(latest))}\n\n"

        # 2. Stream new readings from queue
        while True:
            try:
                msg = _sse_queue.get(timeout=30)
                yield f"data: {json.dumps(msg)}\n\n"
            except queue.Empty:
                yield ": heartbeat\n\n"  # keep-alive

    return Response(event_stream(), mimetype="text/event-stream")
```

### Key Data Flows

1. **Live reading → gauge update (SSE path):** Collector → queue → SSE endpoint → JS `EventSource.onmessage` → gauge instance `.update({value: newVal})` — latency < 1 second.

2. **History backfill (HTTP path):** Page load → `GET /api/history?limit=288` → `historyRows[]` → `drawChart()` for each line chart. Same as current but now also seeds gauge initial state.

3. **Condition analysis (still server-side):** Collector calls `analyze_weather()` or `_simple_condition()` inline, attaches `condition`, `icon`, `description` to the reading dict. These fields flow through the same SSE/HTTP paths — no new server work needed.

---

## JavaScript Architecture

### Structure Recommendation

```javascript
// Single IIFE to avoid globals (still inline in _DASHBOARD_HTML or a template constant)
const wxDashboard = (function() {
    // ── State ──────────────────────────────────
    const state = {
        historyRows: [],
        lastId: 0,
        failCount: 0,
    };

    // ── Chart Registry ──────────────────────────
    const charts = {};  // { tempChart: Chart instance, humChart: ..., gaugeTemp: ..., gaugeHum: ... }

    // ── Helpers ─────────────────────────────────
    function windDirStr(d) { /* unchanged from current */ }
    function beaufort(ms)   { /* unchanged */ }
    // ... icon functions unchanged

    // ── Line Charts (existing, enhanced animation) ──
    function drawLineChart(canvasId, rows, key, color, fromColor) {
        // same as current but enhanced animation: { duration: 800, easing: 'easeOutQuart' }
    }

    // ── Gauge Widgets (NEW) ─────────────────────
    function createGauge(canvasId, config) {
        // config: { min, max, segments: [{value, color}], unit, label }
        // returns Chart.js gauge instance via chartjs-gauge plugin
    }

    function updateAllGauges(reading) {
        // gaugeTemp.update(temperature_C)
        // gaugeHum.update(humidity)
        // gaugeWind.update(wind_avg_m_s)
        // gaugeRain.update(rain_today_mm)
    }

    // ── Current Conditions Panel (NEW) ──────────
    function updateConditionsPanel(reading) {
        // Refreshes a dedicated hero section with:
        //   - condition icon (large)
        //   - condition text (from Ollama/rule analysis)
        //   - prominent temperature + "Feels like"
        //   - last updated timestamp
    }

    // ── Data Layer ──────────────────────────────
    function handleNewReading(reading) {
        state.historyRows.push(reading);
        if (state.historyRows.length > 288) state.historyRows.shift();
        updateConditionsPanel(reading);
        updateAllGauges(reading);
        // Append point to line charts (no full redraw)
        appendChartPoint(charts.tempChart, reading.time, reading.temperature_C);
        appendChartPoint(charts.humChart,  reading.time, reading.humidity);
        appendChartPoint(charts.windChart, reading.time, reading.wind_avg_m_s);
        appendChartPoint(charts.rainChart, reading.time, reading.rain_today_mm);
    }

    function appendChartPoint(chart, time, value) {
        // More efficient than full destroy+recreate
        chart.data.labels.push(time.slice(11,16));
        chart.data.datasets[0].data.push(value);
        if (chart.data.labels.length > 288) {
            chart.data.labels.shift();
            chart.data.datasets[0].data.shift();
        }
        chart.update('none');  // no animation for streaming append
    }

    // ── SSE Connection (NEW) ───────────────────
    function connectSSE() {
        const es = new EventSource('/api/stream');
        es.onmessage = (e) => {
            const reading = JSON.parse(e.data);
            if (reading.id && reading.id > state.lastId) {
                state.lastId = reading.id;
                handleNewReading(reading);
            }
        };
        es.onerror = () => {
            // EventSource auto-reconnects; increment failCount like current code
            state.failCount++;
            if (state.failCount >= 3) {
                // Fall back to polling
                es.close();
                startPollingFallback();
            }
        };
        return es;
    }

    // ── Polling Fallback (existing) ────────────
    // Keep the existing setInterval(refresh, 60000) as fallback

    // ── Init ────────────────────────────────────
    function init() {
        // 1. Initial fetch: /api/history + /api/current
        // 2. Create line charts
        // 3. Create gauges
        // 4. Connect SSE
        // 5. Set polling fallback timer
    }

    return { init };
})();

document.addEventListener('DOMContentLoaded', wxDashboard.init);
```

### Why This Structure

- **IIFE module** keeps everything off the global scope — important when the dashboard JS lives in a Python string that will be edited alongside Python code.
- **Central `state` object** makes data flow traceable; view functions are pure renderers of state.
- **Chart registry** (`charts{}`) avoids the `window[key2]` hack currently used. Cleaner and debuggable.
- **SSE + polling fallback** gives real-time when available, degrades gracefully.

---

## Template Organization

### Current Layout (rough proportions)

```
_DASHBOARD_HTML ~540 lines
  ├── <head> ~30 lines (meta, CDN script, Google Fonts import)
  ├── <style> ~230 lines (CSS variables, cards, charts, responsive)
  ├── <body> ~80 lines (HTML structure)
  └── <script> ~200 lines (all JS)
```

### Recommended Layout With New Features

```
_DASHBOARD_HTML ~350 lines (just HTML + minimal <head>/<style>)
  ├── <head> ~30 lines (meta, CDN scripts: Chart.js + chartjs-gauge + fonts)
  ├── <style> ~280 lines (CSS: existing + gauges + conditions panel + animations)
  ├── <body> ~120 lines (HTML: existing + conditions panel hero + gauge containers)

_DASHBOARD_JS   ~350 lines (all JavaScript)
  ├── IIFE module setup ~10 lines
  ├── Helpers ~60 lines (windDirStr, beaufort, icons — unchanged)
  ├── State & chart registry ~15 lines
  ├── Line chart functions ~40 lines (existing, modified for incremental update)
  ├── Gauge functions ~60 lines (createGauge, updateAllGauges)
  ├── Conditions panel ~40 lines (updateConditionsPanel)
  ├── Data layer ~60 lines (handleNewReading, appendChartPoint)
  ├── SSE connection ~50 lines (connectSSE, polling fallback)
  └── Init ~15 lines
```

### Route Serving Pattern

```python
_DASHBOARD_HTML = r"""..."""  # ~350 lines
_DASHBOARD_JS = r"""..."""     # ~350 lines

@app.route("/dashboard.js")
def dashboard_js():
    return flask.Response(_DASHBOARD_JS, mimetype="text/javascript")

@app.route("/")
def dashboard():
    # Replace placeholders and serve HTML
    return _DASHBOARD_HTML.replace("__HOSTNAME__", hostname) ...
```

This keeps single-file deployment (no filesystem dependency) while separating concerns for editing.

---

## Scaling Considerations

| Scale | Approach | Notes |
|-------|----------|-------|
| Single user (current) | SSE queue + Flask threaded | Works with `app.run(threaded=True)` (default); one SSE connection is trivial |
| 2-5 concurrent viewers | Same architecture | Flask threaded server handles concurrent SSE + API requests fine for local network |
| 10+ concurrent viewers | Switch to gunicorn + gevent workers | SSE connections block worker threads; need async workers at scale |
| 50+ viewers | Add Redis Pub/Sub for SSE queue | Decouples collector from web workers; allows horizontal scaling |

### Practical ceiling for current architecture

The existing deployment (Flask dev server, single process) handles this dashboard's user base of 1-3 people on a LAN. SSE with `queue.Queue` works fine here. The bounded queue (`maxsize=50`) prevents memory growth if no browser is connected. No Redis or gunicorn needed.

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Full chart destroy+recreate on each update

**Current code does this** — `drawChart()` calls `window[key2].destroy()` then `new Chart(...)`. This causes a visible flicker, loses animation state, and is wasteful for streaming data.

**Do this instead:** Use `chart.data.labels.push()` / `chart.data.datasets[0].data.push()` and `chart.update('none')` for appending points. Only recreate on full data reset (e.g., history reload).

### Anti-Pattern 2: Multiple JS libraries for visualization

**What people do:** Use Chart.js for line charts, JustGage for gauges, D3.js for something else. Three libraries, three CDN downloads, visual inconsistency.

**Do this instead:** Use Chart.js + chartjs-gauge — unified rendering, shared animation system, consistent theming. The chartjs-gauge plugin is 4.77 KB minified.

### Anti-Pattern 3: Polling-only data flow after SSE is added

**What happens:** SSE works fine, but the polling interval keeps firing anyway, wasting bandwidth and causing visual jitter as stale data overwrites fresh SSE data.

**Do this instead:** SSE is the primary path. Polling is the fallback — only fire `/api/current` and `/api/history` on SSE connection loss. The timer acts as a watchdog, not a heartbeat.

### Anti-Pattern 4: Building gauges from scratch with canvas/SVG

**What people do:** Hand-roll gauge widgets because "it's just an arc and a needle." This takes way longer than expected — edge cases in needle math, segment shading, responsive sizing, animation easing.

**Do this instead:** Use chartjs-gauge which solves all of these on top of Chart.js's battle-tested rendering pipeline.

---

## Integration Points

### External Services (New Dependency)

| Service | Integration | Why Not Something Else |
|---------|-------------|----------------------|
| chartjs-gauge from CDN | `<script src="https://cdn.jsdelivr.net/npm/chartjs-gauge@0.3.0/dist/chartjs-gauge.min.js">` | Tiny (4.77 KB), no build step, registers as Chart.js plugin automatically |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| Collector ↔ SSE Queue | `_sse_queue.put(msg)` after SQLite insert | Must happen after all analysis/rain computation is attached to msg |
| SSE Queue ↔ SSE Endpoint | `_sse_queue.get(timeout=30)` with heartbeat | Blocking get; timeout ensures heartbeats when no new data |
| SSE Endpoint ↔ JS Data Layer | `EventSource.onmessage` → `handleNewReading()` | JSON deserialization; always check `reading.id > lastId` for dedup |
| JS Data Layer ↔ Gauges | `gaugeInstance.update({value: ...})` | chartjs-gauge native API; triggers animated needle transition |
| JS Data Layer ↔ Line Charts | `appendChartPoint(chart, time, value)` | Direct data manipulation on Chart.js instance; no destroy/recreate |

---

## Build Order for Implementation

The implementation should follow dependency order. Each phase produces a working, shippable state.

### Phase 1: SSE Data Pipeline + JS Extraction

**Why first:** Foundation. Everything else depends on cleaner JS structure and real-time data flow.

- Extract JS from `_DASHBOARD_HTML` into `_DASHBOARD_JS` constant; serve via `/dashboard.js`
- Add `_sse_queue` (bounded `queue.Queue`) and wire collector to push after insert
- Add `/api/stream` SSE endpoint
- Refactor JS into IIFE module structure
- Connect `EventSource` in the browser; keep polling as fallback

**Ship state:** Dashboard works identically visually, but JS is structured and data flows via SSE. Polling still fires but is redundant.

### Phase 2: Gauge Widgets

**Why second:** Depends on Phase 1's data flow to get live updates to gauge components.

- Add chartjs-gauge CDN script to page `<head>`
- Write `createGauge()` and `updateAllGauges()` functions
- Add gauge canvas elements to HTML (new `.gauges` grid section, or replace card values)
- Animated needle transitions on each SSE event
- Choose placement: inline with cards vs dedicated gauges row

**Ship state:** Dashboard has four animated gauge widgets receiving live updates. Line charts still work.

### Phase 3: Current Conditions Panel

**Why third:** Pure UI lift; all data already flows through Phase 1. No new server code.

- Design hero panel layout (large condition icon, condition text, prominent temp)
- Write `updateConditionsPanel()` that reads condition/icon/description from the reading dict
- Position above or alongside the existing card grid
- Add visual emphasis: larger font, subtle background glow, animated border

**Ship state:** Complete dashboard: current conditions hero + gauges + line charts + status bar.

### Phase 4: Animation Polish

**Why last:** Visual refinement; no structural changes.

- Enhanced chart animations (easeOutQuart, longer duration, stagger)
- Gauge needle transition smoothing
- Current conditions panel entrance/update animation
- Loading state handling (skeleton screens instead of "--" placeholders)

**Ship state:** Polished, production-quality dashboard.

---

## Phase Dependency Graph

```
Phase 1 (SSE + JS structure)
    │
    ├──► Phase 2 (Gauges) ──► requires SSE data flow + modular JS
    │
    └──► Phase 3 (Conditions Panel) ──► requires modular JS + SSE
              │
              └──► Phase 4 (Animation Polish) ──► all components exist
```

Phase 1 is the critical path. Phases 2 and 3 can be done in parallel after Phase 1 if desired, but sequential is recommended to keep each diff focused.

---

## Gauge Implementation Detail (chartjs-gauge)

### Temperature Gauge Example

```javascript
// Segments: cold (blue), moderate (green), warm (yellow), hot (red)
function createTempGauge(canvasId) {
    const ctx = document.getElementById(canvasId).getContext('2d');
    return new Chart(ctx, {
        type: 'gauge',
        data: {
            datasets: [{
                value: 20,                         // needle position (updated live)
                minValue: -20,                     // gauge floor
                data: [10, 20, 30, 50],            // segment boundaries (cumulative from minValue)
                backgroundColor: [
                    'rgba(0, 150, 255, 0.7)',     // -20 to -10: cold blue
                    'rgba(0, 200, 100, 0.7)',     // -10 to 10: moderate green
                    'rgba(255, 200, 0, 0.7)',     // 10 to 40: warm yellow
                    'rgba(255, 50, 50, 0.7)',     // 40 to 90: hot red (cap at 50 anyway)
                ],
                borderWidth: 0,
            }],
        },
        options: {
            needle: {
                radiusPercentage: 2,
                widthPercentage: 3,
                lengthPercentage: 80,
                color: 'rgba(192, 202, 245, 0.9)',  // --text color
            },
            valueLabel: {
                display: true,
                formatter: (v) => `${v.toFixed(1)}°C`,
                color: '#c0caf5',
                backgroundColor: 'rgba(13, 17, 23, 0.8)',
                borderRadius: 6,
                padding: { top: 6, bottom: 6, left: 10, right: 10 },
            },
            cutout: '70%',          // thin arc
            rotation: -90,          // start at 9 o'clock
            circumference: 180,     // half circle
            plugins: { legend: { display: false } },
        },
    });
}

// Update on new reading:
function updateTempGauge(value) {
    const chart = charts.gaugeTemp;
    chart.data.datasets[0].value = value;
    chart.update();  // animates needle to new position
}
```

### Gauge Design Per Metric

| Metric | Canvas ID | Range | Segments | Color Theme |
|--------|-----------|-------|----------|-------------|
| Temperature | `gaugeTemp` | -20 to 50°C | 4 zones (cold/moderate/warm/hot) | Cyan→Green→Yellow→Magenta (matches card theme) |
| Humidity | `gaugeHum` | 0 to 100% | 3 zones (dry/comfortable/humid) | Cyan shades |
| Wind Speed | `gaugeWind` | 0 to 30 m/s | 4 zones (calm/moderate/strong/gale) | Green shades |
| Rain | `gaugeRain` | 0 to 50 mm | 3 zones (light/moderate/heavy) | Yellow→Magenta gradient |

---

## Current Conditions Panel Layout

```
┌─────────────────────────────────────────────────────────┐
│  ┌──────────────┐                                        │
│  │              │  ┌──────────────────────────────────┐  │
│  │   🌧️         │  │  Rainy & Windy                   │  │
│  │   (large     │  │  Rain at 14.2°C with breeze      │  │
│  │    icon)     │  │                                   │  │
│  │              │  │  Updated: 12:34:05 · Live        │  │
│  └──────────────┘  └──────────────────────────────────┘  │
│                                                          │
│  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐     │
│  │ 14.2°│  │  72% │  │ 3.4  │  │ 8.2° │  │ 2.1mm│     │
│  │ Temp │  │ Hum  │  │Wind m│  │ Dew  │  │ Rain │     │
│  └──────┘  └──────┘  └──────┘  └──────┘  └──────┘     │
└─────────────────────────────────────────────────────────┘
```

This panel replaces or sits above the current card grid. The key addition is surfacing the `condition` and `description` fields (already computed server-side) in a prominent hero position.

---

## Sources

- [sourcentis/chartjs-gauge](https://github.com/sourcentis/chartjs-gauge) — Chart.js plugin for gauge charts with animated needle (v0.3.0, 4.77 KB minified)
- [Chart.js Doughnut documentation](https://www.chartjs.org/docs/latest/charts/doughnut.html) — underlying API for gauge arc configuration
- [Flask SSE without dependencies](https://maxhalford.github.io/blog/flask-sse-no-deps/) — queue-based SSE pattern compatible with threaded Flask
- [Flask SSE streaming pattern](https://nflatrea.bearblog.dev/real-time-notifications-with-flask-and-sse/) — confirmation that `app.run(threaded=True)` supports SSE
- [MDN: EventSource API](https://developer.mozilla.org/en-US/docs/Web/API/EventSource) — browser API for SSE consumption, auto-reconnect behavior
- [JustGage](https://toorshia.github.io/justgage/) — alternative gauge library (rejected: would add separate dependency)
- Existing codebase: `wx_server.py` lines 608-1149 — current dashboard implementation to refactor

---

*Architecture research for: wxstat dashboard visualization upgrade (DASH-01, DASH-02, DASH-03)*
*Researched: 2026-07-21*
