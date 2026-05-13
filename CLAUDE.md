# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Layout

The actual application lives in the `jav_meta/` subdirectory. The repository root only contains `README.md`. All development commands must be run from `jav_meta/`.

## Commands

All Python scripts run via `uv` (never `python` directly):

```bash
# Install deps and create .venv (run inside jav_meta/)
uv sync

# CLI entry point
uv run javdb --help
uv run javdb init
uv run javdb crawl javbus --full
uv run javdb crawl javbus --incremental
uv run javdb crawl javbus --pages 1-100
uv run javdb selftest
uv run javdb stats

# Tests are standalone scripts (pytest is not a dependency)
uv run python tests/test_quick.py
uv run python tests/test_crawl_smoke.py
```

## Architecture

### Async Crawl Pipeline

```
CrawlOrchestrator
  ├── AdaptiveClient      (httpx + AdaptiveSemaphore for concurrency)
  ├── JavBusScraper       (list-page + detail-page parser)
  ├── ImageDownloader     (async cover/screenshot/avatar downloads)
  └── AutoHealer          (feedback loop: reduces concurrency / switches fallback selectors)
```

- `AdaptiveClient` uses a custom `AdaptiveSemaphore` (via `asyncio.Condition`) to safely adjust concurrency at runtime. It adapts down on HTTP 429/502/503/504 or connection errors, and adapps up on first-attempt successes.
- `JavBusScraper` has two CSS selector profiles: `primary` and `fallback`. AutoHealer triggers fallback after 3 consecutive empty pages.
- `CrawlOrchestrator.run_full()` auto-resumes from the latest `CrawlLog` with `status=running/failed` if `checkpoint_page > 1`.
- `CrawlOrchestrator` writes a `progress.json` every 180 seconds and dumps SQLite to SQL every 50 pages (`DUMP_INTERVAL_PAGES`).

### Database

SQLite with SQLAlchemy 2.0. The engine uses `NullPool` to avoid threading/pool locking issues. `SessionLocal` is instantiated per operation — never shared across async tasks.

Key tables: `movies`, `actresses`, `genres`, `screenshots`, `magnets`, `sources`, `crawl_logs`. Many-to-many joins: `movie_actress`, `movie_genre`.

Image paths are stored as relative POSIX paths (forward slashes) against `settings.project_root`.

### Configuration

All tunables live in `jav_meta.config.settings` (Pydantic `BaseSettings`). Values are loaded from `jav_meta/.env`. At minimum, `JAVBUS_COOKIE` must be set.

## Critical Constraints

**Never run `taskkill /F /IM python.exe`.** This kills all Python processes indiscriminately, including the current Claude Code session. If a stale Python process holds a SQLite lock (causing `database is locked`), identify its PID with `Get-Process python` and kill only that specific PID.

**Crawl tasks must execute to completion.** A single shell invocation has a 300-second timeout. For full crawls, use a loop: run `uv run javdb crawl javbus --full`, check `CrawlLog` status when it times out, and restart if status is still `running` or `failed`. The orchestrator auto-resumes from its checkpoint.

**Code and documentation must stay in sync.** After any code change, verify `jav_meta/README.md` is still accurate.

## Agent Discipline

### Commit Policy

Any file modification (code fix, config update, doc change) must be committed immediately: `git add` + `git commit`. Do not wait for "all changes done" or user prompt.

### Pre-/Post-Change Checklist

Before submitting changes, verify:

1. **Deprecated API scan** — No `datetime.utcnow()`, no `imp`, no `asyncore`.
2. **Concurrency safety** — Any change to `asyncio.Semaphore`, `Lock`, or `Event` must be reviewed for deadlock risk.
3. **No runtime `__import__`** — All imports at file top.
4. **Resource leaks** — `httpx.Client`, DB sessions, file handles must have matching close/release.
5. **Type/interface consistency** — Function signature or model field changes require updating all call sites and ORM mappings.
6. **README sync** — Installation steps, commands, or structure changes must be reflected in `jav_meta/README.md`.

### Testing Closure

All changes must complete the loop: **develop → test → fix → verify**. Never submit untested code.

| Change Scope | Verification |
|---|---|
| Crawler / parser / DB | `uv run python tests/test_crawl_smoke.py` |
| General / quick check | `uv run python tests/test_quick.py` |
| Local file organizer | Manual: `uv run javdb scrape <test-dir>` |
| CLI / config | Manual: `uv run javdb <command>` |

If a test fails, fix it before committing. If blocked, report to user with options.

### Code Style

- All code and comments in English.
- Type hints throughout.
- All network I/O is async; DB writes in the crawler use sync SQLAlchemy sessions.
- No hard-coded URLs, paths, or timeouts — use `settings`.
- Image filenames sanitized via `_safe_filename` (strips Windows-illegal chars, truncates to 200).

## Security

- `JAVBUS_COOKIE` in `.env` is sensitive. `.env`, `data/db.sqlite3`, and `data/images/` are all `.gitignore`d.
- Proxy can be set via `PROXY_URL` for regions where JavBus is blocked.

## Known Code Debt

`jav_meta/crawler/orchestrator.py` still calls `datetime.datetime.utcnow()` in `_persist_movie` (line ~345) and `_dump_sql` (line ~445), despite `models.py` having replaced it with `_utc_now()`. `jav_meta/utils/autoheal.py` also uses `utcnow()`. These should be fixed if the lines are ever touched.
