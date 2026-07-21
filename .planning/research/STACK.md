# Stack Research: Animated Charts, Gauges & Current Conditions

**Domain:** Weather station dashboard visualizations (browser-side)
**Researched:** 2026-07-21
**Confidence:** HIGH

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| **Chart.js** | 4.x (already loaded) | Line charts + gauge widgets | Already in the project. No server-side change. For DASH-01, the animation API is built-in — no upgrade needed. For DASH-02, a Chart.js plugin adds native gauge support with the same rendering pipeline. |
| **@sourcentis/chartjs-gauge** | 1.2.0 | Analog gauge/dial widgets | Chart.js plugin that registers `type: 'gauge'` on the doughnut controller. Animated needle, colored danger/warning/normal segments, value label — everything DASH-02 needs. Ships a UMD build for CDN script-tag loading. |
| **CSS (no library)** | — | Current conditions panel | DASH-03 is a pure layout/visual enhancement. The existing card structure needs a CSS redesign (larger hero panel, animated value transitions). No JS library can improve on well-written CSS transitions for value updates. |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| **Chart.js animation config** | 4.x (built-in) | Smooth chart transitions | Always. Replace the current `{ duration: 400 }` with a richer config: better easing for initial draw, shorter duration for streaming updates. |
| **CSS `transition` property** | — | Animate numeric value changes in cards/gauges | When updating displayed values (temperature, humidity) in the current-conditions panel and gauge value labels. A 300-500ms `transition` on the text element prevents jarring numeric jumps. |

### CDN URLs

```html
<!-- Already loaded — no change needed -->
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>

<!-- Add for gauges (DASH-02) -->
<script src="https://cdn.jsdelivr.net/npm/@sourcentis/chartjs-gauge@1.2.0/dist/chartjs-gauge.umd.js"></script>
```

This is the only new dependency. 6 KB gzipped.

## Installation

No npm install, no build step. Both Chart.js and chartjs-gauge load from CDN via `<script>` tags in the inline dashboard HTML. This is deliberate — the project constraint is `python3 wx_server.py` with zero toolchain.

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| **@sourcentis/chartjs-gauge** (Chart.js plugin, v1.2.0) | **JustGage v2.0.1** (standalone SVG) | If the Chart.js plugin has bugs or insufficient customization. JustGage v2 (Oct 2025 rewrite) is mature, zero-dependency, and has richer gauge styling (arc, donut, differential). Its downside: SVG rendering looks different from Chart.js canvas charts — visual inconsistency. Also 8.6 KB vs 6 KB. |
| **@sourcentis/chartjs-gauge** | **canvas-gauges** (Mikhus) | **Never.** Last updated April 2020. Unmaintained. Do not use. |
| **@sourcentis/chartjs-gauge** | **svg-gauge** (naikus) | **Avoid.** Last release July 2022. Too minimal — no segment coloring, no built-in value animation. 6.9K weekly downloads but stagnant. |
| **@sourcentis/chartjs-gauge** | **chartjs-gauge** (EightTop, v0.3.0) | **Avoid.** Chart.js v2 only. Abandoned since May 2021. The name collision with the sourcentis package is confusing — verify which one you're loading. |
| **@sourcentis/chartjs-gauge** | **chartjs-chart-radial-gauge** (v1.1.0) | Would be a secondary option but builds on Chart.js v3 API. No evidence of Chart.js v4 compatibility. |
| **@sourcentis/chartjs-gauge** | **Custom `afterDraw` needle on doughnut** | If you want zero new dependencies and have time for custom canvas needle-drawing code. Workable but reinventing a solved problem — the plugin already handles needle animation, hit detection, and resize correctly. |
| **Chart.js animation config** (no library) | **GSAP + Chart.js** | Overkill for 4 line charts polling every 60s. GSAP adds 18 KB+ for animation orchestration that Chart.js already provides. |
| **CSS transitions for value animation** | **countUp.js / anime.js** | Unnecessary for this project. Weather values change slowly (1-2°C per refresh). A CSS `transition` on `opacity` or `transform` is sufficient visual polish. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| **canvas-gauges** (Mikhus) | Last updated April 2020. No Chart.js integration. Separate canvas context means zooms and responsive sizing must be managed independently. | @sourcentis/chartjs-gauge (same canvas as Chart.js) |
| **EightTop/chartjs-gauge** (npm `chartjs-gauge` v0.3.0) | Compatible only with Chart.js v2.x. The npm package name is confusingly identical to the sourcentis one but is a different, abandoned project. | @sourcentis/chartjs-gauge |
| **RaphaelJS** | JustGage v1 required this; JustGage v2 dropped it. If you see tutorials mentioning Raphael in 2026, they're stale. | JustGage v2 (zero deps) or @sourcentis/chartjs-gauge |
| **Highcharts / amCharts** | Licensed (non-free for commercial use). Overkill for a personal LAN weather dashboard. | Chart.js 4.x (MIT) |
| **D3.js** | Powerful but wrong tool for this job. You'd need to manually build gauge arc paths, needle SVG, animation loops. D3 is 250 KB+. | @sourcentis/chartjs-gauge (6 KB, pre-built) |
| **Server-side chart generation (Matplotlib)** | Flask would need to render PNGs and serve them as `<img>` — loses interactivity, tooltips, responsive resizing. | Client-side Chart.js |
| **WebSockets / SSE for real-time** | Not needed. Dashboard polls `/api/current` every 60s and `/api/history` on the same interval. Data resolution is 1 reading per minute from the sensor. | Existing `fetch()` + `setInterval(60000)` — keep it. |

## Detailed Recommendations

### DASH-01: Animated Chart Transitions — Configuration Change Only

The existing code uses:
```javascript
animation: { duration: 400 }
```

This is functional but basic. Chart.js 4.x offers a richer animation API that produces visibly smoother transitions for weather data:

**Recommended configuration:**

```javascript
options: {
  animation: {
    duration: 600,              // Slightly slower = smoother for 60s polling
    easing: 'easeOutCubic',     // Default is 'easeOutQuart' — 'easeOutCubic' is gentler
  },
  transitions: {
    // When data updates via chart.update(), use a shorter, snappier transition
    reset: {
      animation: {
        duration: 300,
        easing: 'easeOutQuad'
      }
    }
  }
}
```

**Why this matters for weather data:**
- Current polling is 60 seconds between updates — the chart redraws completely. A 600ms `easeOutCubic` animation makes new data points slide in naturally.
- The `reset` transition (triggered when `chart.update('reset')` is called) provides a shorter 300ms animation for the data-point shift when old points fall off the 288-sample window.
- No need for progressive drawing or per-point animations — weather data has only 288 points (24h at 1/min), well within Chart.js's comfort zone.

### DASH-02: Analog Gauges — Add @sourcentis/chartjs-gauge

**Why this library wins over standalone gauge libraries:**

1. **Same rendering context.** Chart.js renders on `<canvas>`. The gauge plugin extends Chart.js's doughnut controller — so gauges and line charts share the same canvas rendering pipeline, device pixel ratio handling, and responsive resize logic.

2. **Animated needle for free.** The plugin draws and rotates the needle on each animation frame. When you call `chart.update()` with a new `value`, the needle animates to the new position automatically.

3. **Colored zone segments.** Weather gauges benefit from green/yellow/red zones (e.g., temperature: cold=blue, moderate=green, hot=red). The plugin uses a cumulative boundary array (`data: [33, 66, 100]`) that maps cleanly to weather thresholds.

4. **CDN-friendly UMD build.** The `@sourcentis/chartjs-gauge` npm package exposes a UMD build at `dist/chartjs-gauge.umd.js` that registers itself on the global `Chart` object. No bundler needed.

5. **Single gauge per canvas.** Each gauge gets its own `<canvas>` element, meaning you can lay out temperature, humidity, wind, and rain gauges independently in the dashboard grid.

**Example gauge for weather station:**

```javascript
// 180° half-gauge with colored zones and animated needle
new Chart(document.getElementById('tempGauge'), {
  type: 'gauge',
  data: {
    datasets: [{
      value: 22.5,                    // Current needle position
      data: [10, 20, 30, 50],         // Cumulative segment boundaries
      backgroundColor: ['#00f0ff', '#00ff88', '#ffcc00', '#ff3344'],
    }]
  },
  options: {
    needle: {
      radiusPercentage: 2,
      widthPercentage: 3.2,
      lengthPercentage: 80,
      color: '#c0caf5',
    },
    valueLabel: {
      display: true,
      formatter: (v) => v.toFixed(1) + '°C',
      color: '#c0caf5',
      backgroundColor: '#0d1117',
    },
    // Half-gauge defaults (no change needed from plugin defaults)
    rotation: -90,
    circumference: 180,
    cutout: '50%',
  }
});
```

**Risk note:** This is a new library (v1.2.0, 0 stars on GitHub at the time of research). The codebase is small (15 KB) and built on Chart.js's doughnut controller — low complexity. If the project is abandoned, the core functionality (needle drawing on a half-doughnut) is < 100 lines of canvas math and trivial to replicate as a custom Chart.js plugin or standalone function. **Medium-risk, low-consequence dependency.**

### DASH-03: Current Conditions Panel — CSS Enhancement

The existing dashboard already has a "cards" section showing temperature, humidity, wind, dew point, and rain with icons and values. DASH-03 should enhance this into a prominent hero panel, not replace it with a library.

**Recommended approach:**

1. **Elevate the cards to a hero section.** Pull the most critical values (temp, humidity, wind) into a larger, visually distinct panel at the top. Use `grid` with `1fr` columns or a single large card.

2. **Use CSS transitions for value animation.** When `refresh()` updates innerHTML, the browser repaints. Adding a subtle CSS transition on the `.main-val` elements makes changes feel organic:
   ```css
   .main-val { transition: opacity 0.3s, transform 0.3s; }
   ```

3. **Integrate gauges into the current-conditions area.** Temperature gauge sits in the temp card, humidity gauge in the hum card, etc. This gives a combined "big number + dial + trend" display.

4. **No JavaScript animation library needed.** Weather values change by 0.5-2.0 units per 60-second interval. CSS transitions are visually sufficient. Libraries like countUp.js or anime.js would add code for effects that users won't perceive at this update cadence.

## Stack Patterns by Variant

**If the dashboard gains real-time push (WebSocket / SSE) with < 1s updates:**
- Keep Chart.js animation but reduce `duration` to 200-300ms for frequent updates
- Consider disabling animation entirely via `chart.update('none')` for high-frequency data
- Add data decimation: limit the sliding window to 120 points (2 hours at 1/min) instead of 288

**If more than 8 gauges are needed on one dashboard:**
- Each gauge = one Chart.js instance = one canvas. Browsers handle 8-12 canvases fine.
- Beyond 12, consider using `OffscreenCanvas` in a Web Worker, or switch to JustGage (SVG-based, uses DOM elements instead of canvas contexts, typically more efficient at scale).

**If you need to avoid ANY new dependency (philosophical constraint):**
- Implement gauges as a custom Chart.js plugin using the `afterDraw` hook to draw a needle on a doughnut chart. This is ~50 lines of canvas math: calculate angle from value, rotate context, draw triangle for needle, draw circle for pivot.
- Reference: [Chart.js issue #2874 needle pattern](https://github.com/chartjs/Chart.js/issues/2874) or the Stack Overflow "draw a needle on a donut chart" pattern.

## Version Compatibility

| Package | Compatible Chart.js Version | Notes |
|---------|-----------------------------|-------|
| Chart.js 4.x (already loaded) | — | Uses `@4` tag on jsDelivr, currently 4.4.x |
| `@sourcentis/chartjs-gauge` 1.2.0 | Chart.js ^4.x | Auto‑registers `GaugeController` on import. Tested with v4.4.x. |
| JustGage 2.0.1 | N/A (standalone) | Zero dependencies. SVG-based. Loads from `justgage.umd.js`. |
| EightTop chartjs-gauge 0.3.0 | Chart.js ^2.8.0 ONLY | **Do not use.** Incompatible with Chart.js 4.x. |

**CDN URL verification:**
- Chart.js 4.x: `https://cdn.jsdelivr.net/npm/chart.js@4` (resolves to latest 4.x)
- sourcentis gauge: `https://cdn.jsdelivr.net/npm/@sourcentis/chartjs-gauge@1.2.0/dist/chartjs-gauge.umd.js` (6 KB gzipped)

Both load in `<script>` tags before your dashboard JavaScript. The gauge plugin must load AFTER Chart.js (it calls `Chart.Chart.register(GaugeController, ArcElement)` on import).

## Sources

- [Chart.js Animation Documentation](https://www.chartjs.org/docs/latest/configuration/animations.html) — All easing functions, transition modes, callback API. **Confidence: HIGH (official docs)**
- [Chart.js Performance Guide](https://www.chartjs.org/docs/latest/general/performance.html) — Streaming best practices, decimation, animation tradeoffs. **Confidence: HIGH**
- [@sourcentis/chartjs-gauge GitHub](https://github.com/sourcentis/chartjs-gauge) — Full README, CDN usage, compatibility table, examples. **Confidence: MEDIUM (new library, 0 stars, but well-documented and MIT)**
- [JustGage v2 npm](https://www.npmjs.com/package/justgage) — v2.0.1 (Oct 2025), zero dependencies, 144+ tests. **Confidence: HIGH (mature, actively maintained)**
- [canvas-gauges](https://github.com/Mikhus/canvas-gauges/) — Last commit April 2020. **Confidence: HIGH (confirming abandonment)**
- [jsDelivr CDN listing for @sourcentis/chartjs-gauge](https://cdn.jsdelivr.net/npm/@sourcentis/chartjs-gauge@1.2.0/dist/) — Confirms UMD and ESM builds available. **Confidence: HIGH**

---
*Stack research for: wxstat dashboard visualization enhancements (DASH-01, DASH-02, DASH-03)*
*Researched: 2026-07-21*
