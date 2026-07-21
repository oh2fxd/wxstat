# Pitfalls Research: Weather Station Dashboard Visualizations

**Domain:** Real-time weather dashboard with animated charts, analog-style gauges, and current conditions panel
**Researched:** 2026-07-21
**Confidence:** HIGH

## Critical Pitfalls

### Pitfall 1: Full-Chart Redraw Every Frame Kills Animation Performance

**What goes wrong:**
Chart.js redraws the entire chart canvas on every `update()` call — every dataset, every axis, every element. When adding per-frame animations (needle sweeps, value transitions) on top of the existing 60-second poll cycle, this full redraw model causes visible stutter, dropped frames, and eventually browser lockup as data accumulates. The project already polls `/api/current` every 60s; adding animation-driven updates on top of that doubles the render work.

**Why it happens:**
Chart.js is designed for batch-data dashboards where data arrives infrequently, not for streaming animation. Each `chart.update()` walks all datasets, recalculates scales, re-renders every element, and triggers a full canvas paint. With even modest data (e.g., 7 days of 5-minute readings = ~2,000 points per series), this redraw takes 50-200ms — well over the 16.7ms budget for 60fps. Developers assume Chart.js handles animation natively (it does for initial render), but ongoing animated updates progressively degrade as data accumulates because the full dataset is re-processed each frame.

**How to avoid:**
- Use `chart.update('none')` instead of `chart.update()` for animation frames — this skips the animation engine and reduces overhead significantly (observed 200ms → 30ms in benchmarks).
- Decimate data before passing to Chart.js — cap visible points to ~800-1,000 per series using the built-in decimation plugin or manual downsampling. The screen is only ~1,200px wide; there's no benefit to plotting 2,000+ points.
- Disable animations (`animation: false`) on charts that update frequently and reserve animation only for the initial render or transitions.
- For the needle/gauge animation specifically, draw it as a separate Canvas overlay or SVG layer that doesn't trigger full chart redraws.
- Consider using `chart.render()` instead of `chart.update()` when only the visual presentation changes (not the data).

**Warning signs:**
- Chrome Performance tab shows `_updateDatasets()` consuming >40% of frame budget
- Tooltip hover or scroll becomes laggy after dashboard has been running 30+ minutes
- CPU fan spins up after a few polling cycles
- `performance.now()` wrapped around `chart.update()` consistently exceeds 50ms

**Phase to address:**
DASH-01 (Animated chart transitions) and DASH-02 (Gauge widgets). Both must respect the render budget — gauge animations must not trigger full chart redraws.

---

### Pitfall 2: Cumulative Data Growth Without Eviction

**What goes wrong:**
The weather station stores every sensor reading (one per ~60s) indefinitely. After a week that's ~10,000 readings per sensor. After a month it's ~43,000. Chart.js carries this entire array in memory and re-processes it on every update. Without a data retention cap in the frontend, the chart slows linearly with time — what works on day 1 becomes unusable by week 3.

**Why it happens:**
The backend stores all data (SQLite), so the `/api/history` endpoint returns everything. The frontend naively loads the full dataset and pushes it into Chart.js. The natural inclination is "more data is better" — but the browser's memory and render budget are finite. Chart.js documentation notes that performance degrades significantly past ~10,000 data points per dataset, especially when animation is enabled.

**How to avoid:**
- Always enforce a frontend data window — cap at 24-72 hours of 5-minute readings (~288-864 points per series). This is also better UX: a weather dashboard showing 2 weeks of sub-minute readings is noise, not insight.
- Add a backend parameter to `/api/history` (e.g., `?hours=24&aggregate=5min`) that returns decimated data server-side.
- Use SQLite's `strftime` grouping to aggregate to 5-minute or 15-minute averages for the history view.
- Keep the last 2 hours of raw readings for "current conditions" precision, then thin out for historical display.
- Free the point arrays when they exceed a threshold rather than letting them grow unbounded.

**Warning signs:**
- Chart load time increases week-over-week (not day-over-day)
- Browser devtools heap snapshot shows point arrays dominating memory
- Chart.js update time correlates with dataset length, not screen width
- Dashboard restart "fixes" slowness (because data resets)

**Phase to address:**
DASH-01 (Animated charts) — implement data capping *before* adding animations, or the animation work will be wasted on a fundamentally slow render.

---

### Pitfall 3: Animated Gauges Built on Chart.js Doughnut Hacks Break at Small Sizes

**What goes wrong:**
The planned approach ("use Chart.js for gauges too") relies on repurposing a doughnut chart as a gauge — setting `circumference: 180`, `rotation: -90`, overlaying a custom-drawn needle via the `afterDraw` plugin hook, and animating the needle per update. This works in the demo but breaks in practice: the needle position depends on canvas-relative pixel coordinates that shift on resize, the gauge doesn't scale proportionally below ~150x150px, and the custom needle plugin must re-implement hit-testing, accessibility, and resize handling that a purpose-built gauge library or raw SVG gives for free.

**Why it happens:**
Chart.js doughnut-based gauges are a community workaround (see `chartjs-gauge` npm packages, none updated past Chart.js v4, last published 2019-2022). The needle is drawn via direct Canvas API calls in a plugin hook — these coordinates are in canvas-pixel space, not chart-data space. When `responsive: true` resizes the chart, the needle calculations break unless the plugin explicitly handles resize. The HMI gauge literature confirms that built-in gauge primitives degrade visually below 80-100px due to fixed internal padding ratios — and Chart.js doughnut-based gauges suffer the same issue because the `cutout` percentage and needle length don't scale linearly at small container sizes.

**How to avoid:**
- Use a dedicated gauge implementation — either an SVG-based gauge (better for responsiveness, accessibility, and crisp rendering at all sizes) or a lightweight gauge-specific library (e.g., justgage, gauges.js, or a 50-line custom SVG component).
- If using Chart.js for gauges, budget significant testing time for resize behavior, small-viewport rendering, and high-DPI display correctness. Set a minimum container size of 150x150px and test at 100x100px, 75x75px, and 50x50px.
- For SVG gauges: use `stroke-dasharray` and `stroke-dashoffset` on arc paths rather than angle trigonometry — this is the proven pattern from HMI/SCADA gauge implementations and scales perfectly at any size.
- Ensure needle position is calculated from chart scale values, not pixel offsets — the `chart.getDatasetMeta(0).data[0].getCenterPoint()` API provides data-space coordinates.
- Test on a 4K display (devicePixelRatio = 2+) where Canvas-based needle rendering often blurs without explicit DPR compensation.

**Warning signs:**
- Gauge needle doesn't point to the correct value after browser resize
- Gauge looks crisp on desktop but blurry on Retina/4K displays
- Gauge arc / needle visibly misaligned at mobile widths
- The `afterDraw` plugin hook accesses `chart.width`/`chart.height` directly (positional fragility)
- Console shows "Canvas area exceeds maximum" warnings on large displays

**Phase to address:**
DASH-02 (Gauge widgets) — this is the core risk of the gauge implementation choice. Address before building.

---

### Pitfall 4: Information Overload — The Dashboard Shows Everything Equally

**What goes wrong:**
Adding a current conditions panel, three gauges (temperature, humidity, wind), plus the existing four line charts creates a dense information wall where nothing stands out. The user's eye has no hierarchy — every widget has equal visual weight, equal color saturation, equal border prominence. The result is a dashboard that users glance at for 2 seconds and leave without extracting the most important information (e.g., "it's raining" or "wind just picked up").

**Why it happens:**
Weather data is inherently multivariate — temperature, humidity, wind speed, wind gust, rain rate, rain total, pressure, UV, etc. The natural instinct is to show everything because "it's all weather data." But dashboard design research (DOMO, IBM, multiple UX studies) consistently shows that 5-9 visualizations is the ceiling before users disengage. Weather-specific research (Vesper Sky journal) demonstrates that glance-only design — optimizing for a hero number and grid of tiles — leaves users less informed than if the dashboard led with a dominant story and ranked data by relevance.

**How to avoid:**
- **Establish a clear visual hierarchy:** The current conditions panel should be visually dominant (larger, higher on page, distinct background). Gauges secondary. History charts tertiary.
- **Lead with the story, not the numbers:** Temperature is often NOT the most actionable number. Wind shift, rain onset, or pressure trend may be. Consider a "notable conditions" callout.
- **Group related information:** Temperature + feels-like + trend together. Wind speed + gust + direction together. Rain rate + total together. Don't scatter related data across the page.
- **Use white space deliberately:** Add breathing room between widget groups. Related widgets should have less space between them than between groups.
- **Rank by actionability:** Condition that changes your plans (rain starting, wind picking up) should be visually louder than static data (current temp when it's been stable for hours).
- **Remove what doesn't change:** If a metric has been flat (e.g., pressure at 1013hPa for 6 hours), reduce its visual prominence until it becomes notable.

**Warning signs:**
- The dashboard has 8+ equally-sized widgets with no visual weight hierarchy
- Users say they "can't find the temperature" or "is it raining?"
- All widgets use the same border, background, and font weight
- The dashboard feels "busy" — user feedback includes "too much" or "overwhelming"
- Heatmap tracking shows users only interact with 1-2 widgets out of many

**Phase to address:**
DASH-03 (Current conditions panel) and the layout integration of all three DASH features together. The layout must be designed holistically, not widget-by-widget.

---

### Pitfall 5: 60-Second Poll Interval + Animation = Constant Render Contention

**What goes wrong:**
The dashboard currently polls `/api/current` every 60s. When gauges and animations are added, they'll likely need more frequent updates (every 5-15s for a "live" feel). If both the polling callback and the animation loop fight for Chart.js's render queue, they cause double-redraws per frame, janky needle animation, and increased CPU usage. The browser's 16.7ms frame budget gets consumed by redundant work.

**Why it happens:**
The existing code uses `setInterval(fetchCurrent, 60000)`. Adding gauge animations will likely use `requestAnimationFrame` for smooth needle motion. When a fetch completes mid-animation and triggers `chart.update()`, it interrupts the animation loop and forces a full redraw. The animation then restarts from the previous needle position, causing a visual "snap-back" before continuing. This race condition between data fetching and animation rendering is a classic real-time visualization problem.

**How to avoid:**
- Decouple data ingestion from visual commit: fetch data asynchronously into a buffer, then let a single `requestAnimationFrame`-aligned render loop pick up the latest values. Never call `chart.update()` directly from the fetch callback.
- Use a single update cadence — don't have one timer for data fetch and another for animation. Set a single target interval (e.g., every 5 or 10 seconds) for both fetch + render.
- Implement backpressure: track a `busy` flag and a single `queued` slot so rapid fetches don't stack up. Drop intermediate frames if the renderer is still busy.
- Use `IntersectionObserver` to pause all animation and polling when the dashboard tab is backgrounded. This alone can save 90% of unnecessary render work.
- For needle animation specifically: animate the needle value with `requestAnimationFrame` tweening over ~300-500ms *after* new data arrives, then freeze. Don't run continuous animation — it's wasted frames on a static reading.

**Warning signs:**
- Needle "jumps" to correct position then animates back to old position before reaching the target
- CPU usage stays elevated even with no new data arriving
- Chrome Performance tab shows multiple `update()` calls within a single frame
- Animation stutters consistently at the :00 and :30 minute marks (poll boundaries)

**Phase to address:**
DASH-01 and DASH-02 together — the polling/animation coordination must be architected as one system, not two independent features.

---

### Pitfall 6: Gauges That Are Pretty But Unreadable

**What goes wrong:**
Analog-style gauges look impressive in screenshots but often fail at communicating precise values quickly. Users must visually interpolate needle position against unlabeled tick marks, the color zones (green/yellow/red) may not match local climate ranges, and the numeric readout is relegated to a small center label. The gauge becomes decoration rather than information.

**Why it happens:**
Gauges are a familiar visual metaphor (speedometers, pressure dials), but meteorology data doesn't map cleanly to the 0-to-max paradigm. Temperature ranges vary by season ( -20°C to +40°C in winter vs. +10°C to +35°C in summer). A fixed gauge range either clips values or makes the useful range a tiny sliver of the arc. The HMI research confirms that gauge readability degrades significantly below 80-100px — and widgets crammed into a dashboard grid are often smaller than this.

**How to avoid:**
- **Always pair the gauge with a precise numeric readout** — the needle is for trend/gestalt, the number is for exact value. The number should be larger and more prominent than the gauge itself.
- **Use auto-scaling ranges** that adapt to recent data: show the last 7 days' min/max as the gauge bounds, not the sensor's full hardware range. This makes the needle swing visibly on normal days.
- **Limit gauge usage to 3 metrics maximum** — more than 3 gauges on screen and the readability advantage over simple numbers disappears (research: DOMO, IBM dashboard guidelines).
- **Choose the right arc degree:** 180° (semi-circle) is the most readable at a glance; 270° looks dramatic but makes small needle changes harder to perceive; 360° gauges are nearly useless for precision.
- **Use color zones sparingly:** green/yellow/red zones should map to actionable thresholds for your specific climate, not generic defaults. For a Finnish weather station, "red" at +30°C is meaningless; "red" at +25°C on the feels-like scale is useful.
- **Test with real data:** A gauge that looks balanced in a mockup may sit at 10% utilization 90% of the time in production. The needle should move visibly during normal operating conditions.

**Warning signs:**
- Gauge needle sits at the same position for hours/days (range too wide for real data)
- Users ask "what's the actual temperature?" despite the gauge being visible (numeric readout too small)
- Gauge looks good in the prototype but breaks at dashboard-native sizes (50-80px height in a grid row)
- Color zones are chosen for aesthetics rather than local climate statistics
- The gauge numeric label is smaller than the surrounding chart axis labels

**Phase to address:**
DASH-02 (Gauges). Must include range calculation strategy and readability testing at target sizes.

---

### Pitfall 7: Current Conditions Panel Is Just a Data Dump

**What goes wrong:**
The current conditions panel shows: temperature, humidity, wind speed, wind gust, wind direction, rain rate, rain total today, pressure, UV index, feels-like, dew point, cloud cover, visibility — a wall of labeled numbers that requires significant mental effort to parse. The panel becomes a "data grid with icons" rather than an at-a-glance conditions summary.

**Why it happens:**
The API returns all these values, so the natural impulse is to display all of them. "It's weather data, and the user might want to see any of it." Without editorial curation, the panel grows to show everything, prioritizing completeness over comprehension.

**How to avoid:**
- **Define the 5 most actionable metrics** and show only those in the hero panel. For most weather stations: current temperature, wind speed + direction, rain status (is it raining now?), humidity, pressure trend. Everything else goes below the fold or in tooltips.
- **Translate numbers into decisions, not raw values:** "Feels like 2°C" is more actionable than "Temp: 5°C, Wind: 15 km/h, Humidity: 65%." Show the combined metric rather than the inputs.
- **Use semantic grouping:** Temperature + feels-like together. Wind speed + gust + direction together. Rain rate + "last rain" together.
- **Add trend indicators:** A Δ arrow (+2°C / last hour) provides more context than the raw number alone. "22°C ↓" tells the story faster than "22°C" with a separate trend line.
- **Use icons redundantly with text:** A raindrop icon + "Light rain" is better than either alone — it serves both skimmers and readers.
- **Hide what doesn't change:** If the barometer has been steady for 6 hours, de-emphasize it. Surface it when it starts falling.

**Warning signs:**
- Conditions panel has 10+ labeled numeric fields
- Most numbers have no trend indicator (no context for "is this normal?")
- Users ignore the panel and click into history charts for basic info
- The panel takes more than 3 seconds to parse visually
- Same-panel metrics contradict each other visually (e.g., temperature in red and humidity in green with equal weight)

**Phase to address:**
DASH-03 (Current conditions panel). Must include an editorial curation pass — what to show, what to demote, what to omit.

---

### Pitfall 8: Chart.js Animation Defaults — Bézier Curves, Tension, and Point Rendering

**What goes wrong:**
Chart.js defaults include `tension: 0` (straight lines), `pointRadius: 3` (visible points), and animation enabled. These defaults are designed for general-purpose business charts, not weather dashboards. With even moderate data, visible points create visual clutter, and the combination of animation + point rendering adds ~20-30% to render time. The weather data has natural noise (sensor jitter, wind gusts) that makes smoothed Bézier curves *misleading* — they imply smooth transitions where the actual data has real variance.

**Why it happens:**
Chart.js 3.x/4.x have inherent animation overhead compared to 2.x — documented performance regressions show 2-5x slower update times (180ms vs 855ms for similar data). This is due to changes in the animation engine and dataset controller. Combined with default point rendering and line tension, the overhead adds up quickly. For a weather dashboard with 4 charts × 5-10 series each, this can easily consume 500ms+ per update cycle.

**How to avoid:**
- Set `pointRadius: 0` on all history charts — weather data density makes individual points invisible anyway, and removing them significantly improves render performance.
- Disable Bézier curves (`tension: 0`) — straight lines between weather readings are more honest representations of the data.
- Disable animation on history charts (`animation: false`) — animation adds no value to a static time-series display. Reserve animation for the initial chart mount only.
- Set `parsing: false` and pass pre-parsed `{x, y}` data objects to eliminate Chart.js's data parsing overhead.
- Enable the decimation plugin with `algorithm: 'min-max'` to automatically thin dense data regions.
- Profile with `chart.update('none')` vs `chart.update()` to confirm the performance difference.

**Warning signs:**
- Single chart update takes >100ms on Chrome DevTools performance recording
- Tooltips feel laggy or delayed on hover
- Line charts show curved "waves" through flat data segments (Bézier overfitting)
- Initial chart load triggers visible 500ms+ animation lag before data appears
- Point dots overlap into a solid band on dense data sections

**Phase to address:**
DASH-01 (Animated chart transitions). Must override Chart.js defaults for weather-specific rendering.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Inline HTML template in Python string | One file deployment | No linting, no editor support, no template partials | Acceptable for MVP phase only — refactor if dashboard grows past 3 DASH features |
| CDN-loaded Chart.js (no pinned version) | Zero build step | Breaking changes on unannounced CDN version bumps silently break gauges | Acceptable if locked to exact version in URL (e.g., `chart.js@4.4.7`) |
| Custom needle via `afterDraw` plugin hook | No extra dependency | Fragile on resize, DPR change, or Chart.js version update | Avoid — use SVG overlay or purpose-built gauge |
| Polling instead of WebSocket | Simpler implementation | 60-second latency, redundant server load, no push for real-time | Acceptable for DASH-01; reconsider for DASH-02 if gauge smoothness requires <5s updates |
| All data in one `/api/history` call | Single endpoint | Loads unbounded data into memory, grows linearly with time | Not acceptable — must add `?hours=` and `?aggregate=` parameters |
| `setInterval` for data fetch | Simple timer | Drifts over time, doesn't pause when tab is hidden, races with animation | Replace with `setTimeout` chain + `IntersectionObserver` pause |
| Global CSS in embedded `<style>` | Quick to write | Style conflicts, no scoping, hard to override | Acceptable for single-page dashboard; problematic if widget count > 10 |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| Data fetch + animation loop | Calling `chart.update()` directly from the fetch callback | Fetch into buffer, then let a rAF-aligned render loop pick up latest value — never fetch and update in the same call stack |
| API response format | Assuming `/api/current` returns values in display-ready units (e.g., m/s for wind) | Confirm units match gauge range expectations; transform at the API layer, not in the frontend |
| rtl_433 sensor gaps | Not handling null/missing sensor readings in animation | If a sensor misses a broadcast cycle, the gauge should hold last-known value with a visual "stale data" indicator, not animate to 0 |
| Chart.js CDN version | Loading different Chart.js minor versions between dev and production | Pin exact version in CDN URL; test gauges against that exact version before deploying |
| ESP32 TCP push compatibility | Changing the data format that the dashboard renders | The TCP push format is a fixed contract (see PROJECT.md) — don't modify the backend data shape that the dashboard consumes |
| Ollama weather descriptions | Making the conditions panel depend on AI completion latency | Conditions panel must render instantly from `/api/current`; AI description can lazy-load or stream in |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Unbounded data arrays | Chart load times increase daily, heap grows linearly | Cap frontend data to 24-72h window; aggregate server-side | Week 2 of continuous operation |
| Continuous rAF animation | CPU stays at 20-30% even when tab is backgrounded | Use `IntersectionObserver` to pause when hidden; freeze needle after transition completes | Immediately on dashboard launch |
| Multiple `chart.update()` calls per frame | Frame budget >50ms consistently, dropped frames | Decouple data ingress from render; use single rAF-aligned render loop | When poll + animation overlap |
| Chart.js on hidden datasets | Legend toggles cause 1-second lag (known Chart.js issue #12179) | Don't create hidden datasets; toggle visibility instead of add/remove | When more than ~10 datasets exist |
| SVG overlay misaligned with Canvas | Needle position wrong on Retina or after resize | Keep DPR factor strictly in Canvas matrix; overlay coordinates in CSS pixels | On 4K/Retina displays |
| `setInterval` drift | Polls gradually shift timing, eventually double-firing | Use recursive `setTimeout` with drift correction | After 30-60 minutes of continuous operation |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Exposing raw sensor data in API without bounds checking | Gauge needle at extreme position could confuse or misleadingly alarm user | Cap API values at physically plausible ranges (e.g., temp -40°C to +60°C) |
| No validation on `?hours=` parameter | SQL injection or denial of service via huge range requests | Already parameterized (no SQLi), but cap at 168h (7 days) max |
| Frontend assuming API always returns data | Gauge shows 0 or NaN on API failure, looking like a sensor reading | Default to "—" or last-known-good on fetch failure; show stale-data indicator |
| CDN script integrity | Compromised CDN script could inject malicious code into dashboard | Use `integrity="sha384-..."` hash on Chart.js CDN script tag, or bundle locally |
| Exposing detailed weather data on LAN | Low risk for personal project, but data reveals occupancy patterns (when home) | Acceptable for LAN-only; document the risk |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| Equal visual weight for all metrics | User can't find the most important number at a glance | Establish clear hierarchy: conditions panel > gauge > history chart. Use size, position, and color saturation |
| Fixed gauge ranges that don't match climate | Needle never moves (range too wide) or pegs at max (range too narrow) | Auto-scale gauge range from last 7-30 days of data, not sensor hardware range |
| No trend context | User sees "22°C" but doesn't know if that's rising, falling, or normal | Add trend arrows (±2°C/hr), comparison to yesterday, or "normal for this time of day" baseline |
| Animations that take too long | User waits for needle to sweep across gauge before seeing current value | Keep animation duration under 500ms; show numeric value instantly, sweep needle as secondary effect |
| Chart colors too similar | User can't distinguish temperature from humidity line at a glance | Use a consistent, distinguishable palette (temperature = warm color, humidity = cool color, wind = neutral) |
| No empty/error state design | On data fetch failure, chart area goes blank or shows stale frozen data | Show last-known values with "stale" indicator, or show "awaiting data" skeleton |
| All data aggregated at same resolution | Current readings and 7-day-old readings shown at same level of detail | Recent hour: raw readings. Past 24h: 5-min aggregates. Past 7d: hourly aggregates |
| Color-coded gauges without colorblind consideration | 8% of male users can't distinguish red/green zones | Use patterns (stripes, labels) alongside color; avoid red/green as sole differentiator |

## "Looks Done But Isn't" Checklist

- [ ] **Chart animations:** Initial animation works but degrades over time — verify frame rate after 1 hour of continuous data accumulation (not just page load)
- [ ] **Gauge needle:** Needle position correct at page load but drifts after browser resize — verify on viewport width changes, device rotation, and zoom
- [ ] **Current conditions panel:** Shows all data but takes 4+ seconds to parse — verify a user can identify the most important value within 2 seconds
- [ ] **Gauge ranges:** Look correct in mockup but needle stays at 10% in real operation — verify against 7 days of actual production data
- [ ] **Responsive layout:** Works at desktop width but gauges collapse at tablet/mobile — verify at 320px, 768px, and 1920px widths minimum
- [ ] **Data freshness:** Shows new data after polling interval — verify the "last updated" timestamp matches actual data and that stale data indicator works when API is down
- [ ] **Color accessibility:** Looks good to designer — verify with a colorblindness simulator (e.g., Chrome DevTools rendering emulation) for red-green deficiency
- [ ] **Tab visibility:** Animations run fine while tab is focused — verify CPU usage drops when tab is backgrounded and animation resumes correctly on refocus
- [ ] **High-DPI display:** Looks crisp on a standard monitor — verify on a Retina/4K display where canvas gauges often appear blurry without explicit DPR handling
- [ ] **Error recovery:** Works when API responds normally — verify that an API failure doesn't leave the gauge at 0, stuck at last value, or showing NaN

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Full-chart redraw bottleneck | MEDIUM — refactor update calls | Replace `chart.update()` with `chart.update('none')`; implement data decimation; add render throttling |
| Gauge needle misaligned on resize | HIGH — may need full gauge reimplementation | If SVG-based: recalculate stroke-dasharray on resize. If Canvas-based: move needle draw to a resize handler with `chart.resize()` |
| Unbounded data growth causing slowdown | LOW — add frontend data cap | Add `data.splice(0, data.length - maxPoints)` before each chart update; no backend change needed |
| Information overload | MEDIUM — redesign layout hierarchy | Group related widgets, reduce visual weight of less-important metrics, add white space between groups |
| Animation + poll race condition | MEDIUM — restructure update flow | Replace `setInterval` + direct `update()` with buffer-and-rAF pattern; ensure no two updates overlap |
| Color accessibility failure | LOW — update color palette | Replace red/green with blue/orange or add pattern fills; test with Chrome DevTools color vision emulation |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Full-chart redraw every frame | DASH-01 (Animated transitions) | Profile `chart.update()` time with Chrome DevTools; must stay under 16ms per frame |
| Cumulative data growth without eviction | DASH-01 (Animated transitions) | Verify frontend caps data at max 1000 points per series; verify backend aggregation parameter exists |
| Doughnut-hack gauges break at small sizes | DASH-02 (Gauges) | Test all gauge sizes at 100px, 75px, 50px widths; verify on Retina display |
| Information overload — equal visual weight | DASH-03 (Current conditions panel) | Visual hierarchy audit — confirm primary metrics are 2x the size of secondary |
| Poll + animation render contention | DASH-01 + DASH-02 (coordination) | Confirm no `chart.update()` calls originate from fetch callbacks; single render loop per frame |
| Pretty but unreadable gauges | DASH-02 (Gauges) | User test: can you read the exact value from each gauge in under 2 seconds without looking at the numeric label? |
| Conditions panel as data dump | DASH-03 (Current conditions panel) | Panel must show ≤7 primary metrics; secondary metrics collapsed or absent |
| Chart.js animation defaults for weather data | DASH-01 (Animated transitions) | Verify `pointRadius: 0`, `tension: 0`, `animation: false` on history charts |

## Sources

- Chart.js Performance Documentation — https://www.chartjs.org/docs/latest/general/performance.html
- Chart.js Issue #10073 — Performance regression in 3.x vs 2.x (community discussion, 200+ comments)
- Chart.js Issue #11836 — Dataset update performance improvement (PR, merged 2024)
- Chart.js Issue #11977 — Animation optimization when duration = 0 (feature request, 2024)
- Chart.js Issue #12179 — Hidden datasets performance bottleneck (confirmed bug, 2024)
- Chart.js Discussion #10858 — Gauge chart implementation approaches (community)
- Chart.js Discussion #10944 — update() optimization techniques (community best practices)
- IDV Guide — SVG vs Canvas Architecture for Data Visualization (rendering engine tradeoffs)
- IDV Guide — High-Performance Animation & GPU Acceleration (frame budget, layout thrashing)
- IDV Guide — Offscreen Canvas for Background Chart Updates (worker rendering pattern)
- IDV Guide — DOM Impact & Reflow Optimization (layout thrashing prevention)
- Vesper Sky — "Weather, Worth Reading: Designing a Forecast You Finish" (weather UX philosophy)
- DOMO — "Top 10 Dashboard Design Mistakes" (dashboard UX research)
- UIGuides — "How to Design Data Visualizations" (chart typology, data-ink ratio, accessibility)
- IBM Environmental Intelligence Suite — Dashboard guidelines (panel limits, over-alerting)
- HMI Gauge Padding at Small Element Sizes — Industrial Monitor Direct (gauge minimum sizes, SVG approach)
- ROMAIN COUPEY — "Count the Marks, Not the Library Stars" (SVG vs Canvas decision framework)
- Chart.ts — "Canvas vs SVG vs WebGL" (rendering technology comparison)
- FusionCharts — "Canvas vs. SVG: Which is Best for JavaScript Charts?" (library comparison)
- "What I Learned Optimizing JavaScript for High-Frequency UI Updates" (Michael Preston, Feb 2026)
- "Building a High-Performance Real-Time Chart in React" (Dev.to, Aug 2025)
- "Deep Dive: Custom Canvas Charts for 30fps Animation" (slepp.ca, Feb 2026)
- chartjs-gauge npm packages (haiiaaa/chartjs-gauge, sourcentis/chartjs-gauge) — community gauge implementations
- Stack Overflow: Chart.js gauge implementations (multiple threads, 2021-2024)

---

*Pitfalls research for: wxstat weather station dashboard visualization enhancements*
*Researched: 2026-07-21*
