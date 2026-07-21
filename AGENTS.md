<!-- GSD:project-start source:PROJECT.md -->

## Project

**wxstat**

A minimal weather station dashboard for Fine Offset WHx080 sensors. It decodes SDR signals via `rtl_433`, stores readings in SQLite, and serves a web dashboard with live charts, a raw TCP push feed for ESP32 displays, and optional Ollama-powered weather descriptions. Runs as a single Python process.

**Core Value:** Reliable, real-time weather monitoring that never drops a reading — the collector always writes data regardless of dashboard, analysis, or network issues.

### Constraints

- **Compatibility**: Must continue working with existing ESP32 TCP push clients (newline-delimited JSON format is fixed)
- **Deployment**: Must remain runnable as `python3 wx_server.py` — no build step or container required
- **Resilience**: Dashboard changes must never block sensor collection or data storage

<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->

## Technology Stack

## Languages

| Language | Version | Location | Notes |
|----------|---------|----------|-------|
| Python | 3.x (no version pin) | `wx_server.py`, `_apply_fixes.py` | Single-file app, no `pyproject.toml` or `requirements.txt` |

## Runtime

| Component | Detail |
|-----------|--------|
| Python interpreter | `#!/usr/bin/env python3` — no `.python-version` or `pyproject.toml` |
| OS | Linux (primary), macOS (supported) |
| Hardware prerequisite | RTL-SDR compatible USB dongle for `rtl_433` |
| No containerisation | No Dockerfile or docker-compose |

## Frameworks

| Framework | Role | Version (inferred) | Notes |
|-----------|------|--------------------|-------|
| Flask | HTTP server + routing | 2.x or 3.x (not pinned) | Serves dashboard HTML and JSON API |
| Chart.js | Browser-side charting | 4.x (loaded from CDN) | Inline in `_DASHBOARD_HTML` |
| SQLite3 | Embedded database | stdlib | WAL journal mode |

## External Dependencies

| Dependency | How installed | Role | Notes |
|-----------|-------------|------|-------|
| `rtl_433` | System package (`apt` / `brew`) | SDR decoder for Fine Offset WHx080 | **Not** a Python dependency; must be on `$PATH` |
| `flask` | `pip install flask` | Web framework | Only non-stdlib Python dependency |
| Ollama | System daemon (optional) | Local LLM for weather description | On by default; graceful fallback if absent |

## Configuration

| Env var | Default | Purpose |
|---------|---------|---------|
| `HTTP_PORT` | `8085` | Web dashboard port |
| `TCP_PORT` | `8081` | Raw TCP push port (set `0` to disable) |
| `OLLAMA_ENABLED` | `true` | Toggle LLM analysis |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama endpoint |
| `OLLAMA_MODEL` | `qwen2.5:0.5b` | Ollama model name |
| `OLLAMA_CACHE_S` | `300` | Seconds between LLM calls |

## Build / Run

| Script | Purpose |
|--------|---------|
| `start.sh` | One-command launcher |
| `push.sh` | Git add-commit-push helper |
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->

## Conventions

## Code Style

- **Python**: Standard PEP 8-ish, 4-space indentation, single quotes preferred
- **JavaScript**: Inline in HTML template, modern ES6 (arrow functions, `async/await`, `const`/`let`)
- **CSS**: Custom properties (`:root` vars), modern layout (Grid, Flexbox, `clamp()`)

## Naming

- Classes: `PascalCase`
- Functions (public): `snake_case`
- Functions (internal): `_snake_case` (single leading underscore)
- Constants: `UPPER_SNAKE`
- Env vars: `UPPER_SNAKE`

## Error Handling Pattern

- Ollama LLM call fails → fall back to rule-based description
- TCP broadcast to a client fails → disconnect that client silently
- Stats query fails → skip that feature

## Logging

- Ad-hoc `print()` statements, not `logging` module
- Tags per subsystem: `[collector]`, `[tcp-push]`, `[ollama]`, `[server]`
- No log levels, no structured logging
- stderr from rtl_433 subprocess is stored and printed on subprocess exit (not streamed)

## Threading

- `threading.Lock` for shared state (TCP client list, Ollama cache, rain state)
- Daemon threads for background services (collector, TCP accept, stderr drain)
- No thread pool, no asyncio
- All shared state is protected (no data races visible)

## Configuration

- Zero config files — everything via `os.environ.get("KEY", default)`
- Boolean env vars check `("1", "true", "yes")` for truthiness
- No config validation at startup

## Imports

- Standard library first, blank line, third-party second
- No `__future__` imports
- No type annotations anywhere

## Pre-existing Patterns to Preserve

- Single-file deployment simplicity
- Environment-based configuration
- Graceful degradation on external service failure
- Print-based logging with subsystem tags
- Thread-safe shared state with explicit locks

<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->

## Architecture

## Pattern

## Layering

```

```

## Data Flow

```

```

## Thread Model

| Thread | Role | Type |
|--------|------|------|
| Main | Flask `app.run()` | Blocking |
| `_rtl433_loop` | Collector restart loop | `daemon=True` |
| `_run_rtl433` → stdout reader | Process output parser | Main collector loop (runs inside above thread) |
| `_drain_stderr` | rtl_433 stderr logging | `daemon=True` |
| `TCPPushServer._accept_loop` | Accept TCP connections | `daemon=True` |

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

<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->

## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->

## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:

- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->

## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
