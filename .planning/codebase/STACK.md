# Stack: wxstat

**Last updated:** 2026-07-21

## Languages

| Language | Version | Location | Notes |
|----------|---------|----------|-------|
| Python | 3.x (no version pin) | `wx_server.py`, `_apply_fixes.py` | Single-file app, no `pyproject.toml` or `requirements.txt` |

Libraries discovered by scanning the source's `import` statements — **there is no pinned dependency file**. The app will likely break if Flask or its transitive deps undergo a breaking change.

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

All via environment variables — no config files:

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

No build step — Python is interpreted. No CI/CD config found.
