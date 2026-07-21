# Architecture: wxstat

**Last updated:** 2026-07-21

## Pattern

Single-process multi-threaded monolith. Everything lives in `wx_server.py` (1169 lines). The process owns four responsibilities in one address space:

1. **Collector** — daemon thread running `rtl_433` subprocess
2. **TCP Push Server** — daemon thread accepting TCP connections
3. **Flask Web Server** — main thread (blocks on `app.run()`)
4. **Weather Analysis** — called inline during collector loop (Ollama or rule-based)

## Layering

There are no layers — just modules within the single file:

```
┌──────────────────────────────────────┐
│  wx_server.py                        │
│                                      │
│  Database layer                      │
│  ├── init_db() — schema creation      │
│  ├── insert() — write reading         │
│  └── get_db() — Flask per-request DB  │
│                                      │
│  Collector                           │
│  ├── _run_rtl433() — subprocess loop  │
│  ├── _rtl433_loop() — restart loop    │
│  └── _drain_stderr() — logging thread │
│                                      │
│  Analysis                            │
│  ├── analyze_weather() — Ollama+rule  │
│  ├── _simple_condition() — rule-only  │
│  └── _rain_is_active() — rain detect  │
│                                      │
│  TCP Push                            │
│  └── TCPPushServer — socket server    │
│                                      │
│  Web API                             │
│  ├── /api/current  — latest reading   │
│  ├── /api/history  — recent readings  │
│  ├── /api/stats    — today's min/max  │
│  └── /             — dashboard HTML   │
└──────────────────────────────────────┘
```

## Data Flow

```
rtl_433 (SDR) ──stdout JSON──► _run_rtl433()
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
              insert(SQLite)              TCPPushServer.broadcast()
                    │                               │
                    ▼                               ▼
              wxstat.db                     ESP32 / raw TCP clients
                    │
                    ▼
              Flask API (/api/*) ◄── Browser dashboard (Chart.js)
```

## Thread Model

| Thread | Role | Type |
|--------|------|------|
| Main | Flask `app.run()` | Blocking |
| `_rtl433_loop` | Collector restart loop | `daemon=True` |
| `_run_rtl433` → stdout reader | Process output parser | Main collector loop (runs inside above thread) |
| `_drain_stderr` | rtl_433 stderr logging | `daemon=True` |
| `TCPPushServer._accept_loop` | Accept TCP connections | `daemon=True` |

All threads share the SQLite connection (collector writes) and the TCP client list (broadcast). The collector thread creates the SQLite connection; Flask uses a separate per-request connection via `g.db`.

## State Management

- **Database**: Durable, file-backed SQLite (WAL mode)
- **Rain detection**: In-memory `_rain_state` dict with last-seen counter and timestamp
- **Ollama cache**: In-memory `_condition_cache` dict with TTL
- **TCP clients**: In-memory list protected by `threading.Lock`
- **No connection pooling**: SQLite connections created/destroyed per thread

## Entry Points

| Entry Point | File | Function |
|-------------|------|----------|
| `./start.sh` | Shell script | Wrapper that prints URLs |
| `python3 wx_server.py` | `wx_server.py:1156` | `__main__` block — init DB, start collector, start Flask |

## Key Design Decisions

1. **Single file** — zero ceremony deployment, just `python3 wx_server.py`
2. **Environment-only config** — no config files to manage
3. **Cumulative rain delta** — sensor reports cumulative rain since boot; app computes today's delta via SQL query
4. **Graceful degradation** — Ollama failure falls back to rule-based analysis silently
5. **WAL mode** — concurrent reads/writes from collector and Flask without locking issues
