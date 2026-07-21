# Concerns: wxstat

**Last updated:** 2026-07-21

## Technical Debt

| Issue | Location | Severity | Notes |
|-------|----------|----------|-------|
| Monolithic single file | `wx_server.py` (1169 lines) | High | Everything in one file — hard to navigate, test, or reason about |
| No dependency pinning | No `requirements.txt` / `pyproject.toml` | High | Breaking Flask changes will silently break the app |
| No test coverage | Entire codebase | High | Zero tests — regressions must be caught manually |
| Inline HTML template | `wx_server.py:608-1149` (540 lines) | Medium | HTML/CSS/JS embedded as raw string — breaks editor features, no linting |
| Ad-hoc print logging | Throughout | Low | No log levels, no structured output, no log rotation |
| No type annotations | Throughout | Low | No static analysis benefits |

## Bugs / Risks

| Issue | Location | Severity | Notes |
|-------|----------|----------|-------|
| rtl_433 subprocess not in container | `wx_server.py:387-391` | Medium | No Docker — SDR access depends on host system config |
| No graceful shutdown | `wx_server.py:1156-1169` | Medium | `app.run(debug=False)` blocks; SIGTERM may not cleanly close threads |
| Cumulative rain logic on process restart | `wx_server.py:537-547` | Low | After restart, today's rain resets (sensor retains counter, but min value is from DB) |
| No request rate limiting | Flask API | Low | Exposed on LAN with no auth — could be abused if exposed externally |
| SQLite connection per request | `wx_server.py:491-495` | Low | No pooling — fine for single-user but doesn't scale |

## Performance

| Aspect | Assessment |
|--------|-----------|
| Sensor reading throughput | ~1 reading per 60s — well within SQLite's capability |
| Dashboard polling | Browser polls `/api/current` every 60s — negligible load |
| Ollama latency | First inference 5-30s (model load), then cached for 5 min |
| Concurrent TCP clients | Single-threaded select loop, fine for <50 simultaneous clients |
| Memory | ~200-500 MB with Ollama model loaded; <50 MB without |

## Security

| Concern | Detail |
|---------|--------|
| No authentication | All endpoints public on LAN — intentional for embedded use |
| No TLS | HTTP and raw TCP — LAN-only design |
| No input validation | `request.args` passed directly to SQLite (parameterized, so no SQLi) |
| Ollama outbound | To localhost only — no external network exposure in default config |

## Fragile Areas

| Area | Why fragile |
|------|-------------|
| `_rain_is_active()` | Stateful logic dependent on `last_mm` and timing — any bug produces wrong rain display for hours |
| `_simple_condition()` | Deeply nested if/elif — easy to miss boundary conditions (e.g., -5°C with 98% humidity) |
| `_run_rtl433()` exception handling | Broad `except Exception` in outer loop — could mask real issues |
| rtl_433 JSON schema | Assumes specific fields from rtl_433 output format — if rtl_433 changes field names, parsing silently breaks |
| Inline HTML string | Escape sequences and indentation inside a raw Python string are error-prone (see `_apply_fixes.py` which exists purely to fix these) |

## Hardcoded Values

| Value | Location | Should be configurable? |
|-------|----------|------------------------|
| `868.3M` frequency | `RTL_CMD` | Yes — sensors exist on 433.92 MHz and 915 MHz |
| `250k` sample rate | `RTL_CMD` | Maybe — depends on local RF environment |
| `20` gain | `RTL_CMD` | Maybe — depends on antenna/signal strength |
| 30 min rain window | `_RECENT_RAIN_WINDOW = 1800` | Probably — depends on local rainfall patterns |
