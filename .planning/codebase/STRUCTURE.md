# Structure: wxstat

**Last updated:** 2026-07-21

## Directory Layout

```
wxstat/
├── wx_server.py          # Main application (1169 lines) — all logic
├── _apply_fixes.py       # Utility script (48 lines) — applies JS fixes
├── start.sh              # One-command launcher
├── push.sh               # Git commit helper
├── README.md             # Documentation
├── LICENSE               # MIT
├── .gitignore            # Git ignore rules
├── wxstat.db             # SQLite database (auto-created)
├── screenshot.png        # Dashboard screenshot
└── .claude/              # Claude Code context (private)
```

## Key File Details

### `wx_server.py`
- **1169 lines**, single file
- Organized into labeled sections (separator comments):
  - `Database` (lines 46-96) — init, insert helpers
  - `TCP push server` (lines 99-168) — `TCPPushServer` class
  - `Weather condition analysis` (lines 171-348) — Ollama + rule-based
  - `rtl_433 collector` (lines 375-484) — subprocess loop
  - `Flask web routes` (lines 487-601) — API + dashboard
  - `Dashboard template` (lines 604-1149) — inline HTML string
  - `Startup` (lines 1152-1169) — main block

### `_apply_fixes.py`
- **48 lines**, diagnostic script
- Searches for specific JS patterns in `wx_server.py` (tabs vs spaces debugging)
- One-off utility, not part of normal operation

### `start.sh`
- Launcher script that starts `wx_server.py`
- Handles Linux vs macOS differences

### `push.sh`
- Git helper: `git add -A && git commit && git push`

## Naming Conventions

| Convention | Pattern | Examples |
|-----------|---------|---------|
| Files | `snake_case` | `wx_server.py`, `_apply_fixes.py`, `start.sh` |
| Classes | `PascalCase` | `TCPPushServer`, `Flask` |
| Functions (public) | `snake_case` | `init_db()`, `insert()`, `analyze_weather()` |
| Functions (private) | `_snake_case` | `_run_rtl433()`, `_simple_condition()`, `_rain_is_active()` |
| Constants | `UPPER_SNAKE` | `DB`, `RTL_CMD`, `OLLAMA_ENABLED` |
| Config vars | `UPPER_SNAKE` | `HTTP_PORT`, `TCP_PORT` |
| Module vars | `snake_case` | `_condition_cache`, `_rain_state` |

## Code Organisation Notes

- No separate modules/packages — everything in one file
- No `__init__.py`, no `requirements.txt`, no `pyproject.toml`
- Inline HTML template (~540 lines) in a raw string at module level
- Chart.js loaded from CDN (no local assets)
