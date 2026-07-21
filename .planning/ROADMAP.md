# Roadmap: wxstat Dashboard Enhancements

**Created:** 2026-07-21
**Granularity:** Coarse
**Total Phases:** 3
**v1 Requirements:** 10 mapped

---

## Phases

- [ ] **Phase 1: Animated Charts** — Replace destroy+recreate with in-place data updates, animated point transitions, and pulse effect on new data
- [ ] **Phase 2: Analog Gauges** — Temperature, humidity, and wind speed gauge widgets with animated needles and color-coded comfort zones
- [ ] **Phase 3: Current Conditions Panel** — Hero element with prominent temperature, feels-like, high/low ranges, trend arrows, and freshness indicators

---

## Phase Details

### Phase 1: Animated Charts

**Goal:** Charts update smoothly with animated transitions, no visual flicker, and a pulse effect signaling new data — the foundation for all subsequent dashboard enhancements.

**Depends on:** Nothing (first phase)

**Requirements:** CHRT-01, CHRT-02, CHRT-03

**Success Criteria** (what must be TRUE):
1. Charts update data in-place (mutate arrays, call `chart.update()`) instead of destroy+recreate — no flicker visible during updates
2. New data points animate from their previous point position using Chart.js `fromX`/`fromY` callbacks — smooth visual transition observed
3. Chart container pulses with a brief opacity/glow animation when new data arrives — visible feedback without disrupting chart readability
4. All existing line charts (temperature, humidity, wind speed, rain) remain functional with no regressions — data renders correctly on page load and refresh

**Plans:** TBD

**UI hint:** yes

---

### Phase 2: Analog Gauges

**Goal:** Users can read temperature, humidity, and wind speed at a glance through animated, color-coded gauge widgets that feel like physical instruments.

**Depends on:** Phase 1

**Requirements:** GAUG-01, GAUG-02, GAUG-03

**Success Criteria** (what must be TRUE):
1. Temperature gauge displays current value on a 180° half-doughnut with animated needle and four colored comfort zones (blue/green/orange/red) — zone boundaries match safe-to-extreme thresholds
2. Humidity gauge displays current value on a 180° half-doughnut with animated needle and three color zones (dry/comfort/wet) — needle position matches current reading
3. Wind speed gauge displays current value on a 270° arc with animated needle and colored zones — arc range covers expected wind speeds (0–30 m/s)
4. All gauges update live as new readings arrive — needles smoothly transition from old to new position without jarring jumps

**Plans:** TBD

**UI hint:** yes

---

### Phase 3: Current Conditions Panel

**Goal:** Users can glance at the top of the dashboard and immediately understand current weather — temperature, extremes, trends, and data freshness — without scanning charts.

**Depends on:** Phase 1

**Requirements:** COND-01, COND-02, COND-03, COND-04

**Success Criteria** (what must be TRUE):
1. Temperature is displayed as a prominent hero element (larger font, primary position) with feels-like value shown adjacent — hero is visually dominant over other metrics
2. Today's high and low ranges are displayed for key metrics (temperature, humidity, wind) with clear labels — user can see the day's extremes at a glance
3. Each metric shows a trend arrow (▲/▼/→) with a numeric delta value indicating direction and magnitude of change since the last reading — arrows update correctly when values rise, fall, or hold steady
4. Each metric displays a freshness indicator showing elapsed time since the last sensor update (e.g., "2s ago", "30s ago") — indicator clears or updates when new data arrives

**Plans:** TBD

**UI hint:** yes

---

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| Phase 1: Animated Charts | 0/0 | Not started | — |
| Phase 2: Analog Gauges | 0/0 | Not started | — |
| Phase 3: Current Conditions Panel | 0/0 | Not started | — |
