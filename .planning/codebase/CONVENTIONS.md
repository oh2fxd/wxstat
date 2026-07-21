# Conventions: wxstat

**Last updated:** 2026-07-21

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

```
try:
    ... risky operation ...
except Exception:
    pass  # never block readings on analysis failure
```

Non-critical failures are silently caught. The principle is: **never block a sensor reading on analysis failure**. Examples:
- Ollama LLM call fails → fall back to rule-based description
- TCP broadcast to a client fails → disconnect that client silently
- Stats query fails → skip that feature

Only the collector subprocess error prints to stdout: `print(f"[collector] Error: {e}")`.

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
