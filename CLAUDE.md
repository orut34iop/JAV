# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Layout

The actual application lives in the `jav_meta/` subdirectory. All development commands must be run from `jav_meta/`.

## Commands

```bash
uv sync                       # Install deps
uv run javdb --help           # CLI help
uv run javdb init             # Initialize database
uv run javdb selftest         # Pre-flight checks
uv run javdb stats            # Database statistics
uv run javdb queue            # Show discovery queue status

# Crawl (two-phase: discover + download)
uv run javdb crawl --full            # Full: discover all pages + download all
uv run javdb crawl --incremental     # Incremental: discover all + download new only
uv run javdb crawl --download        # Download only: process pending queue items
uv run javdb discover                # Phase 1 only: scan list pages, populate queue
uv run javdb discover --uncensored   # Include uncensored section

# Local file organization
uv run javdb scrape /path/to/movies
uv run javdb search SSIS-001
uv run javdb repair --dry-run        # Show what needs repairing

# Tests (standalone scripts, pytest is not a dependency)
uv run python tests/test_quick.py
uv run python tests/test_crawl_smoke.py
```

## Architecture

### Two-Phase Crawl

```
Phase 1: DISCOVER (fast, list-page-only scan)
  P1 → P2 → ... → empty_page×3 → stop
  For each page: extract (code, detail_url), insert into discovery_queue
  Checkpoint: discovery_checkpoint.json (page number, 6h resume window)

Phase 2: DOWNLOAD (process pending queue)
  For each pending item in discovery_queue:
    1. fetch detail page → MovieData
    2. _persist_movie() → Movie + Actresses + Genres + Screenshots + Magnets + images
  Failed items retried once after all pending processed
```

```
CrawlOrchestrator
  ├── AdaptiveClient      (httpx + AdaptiveSemaphore for concurrency)
  ├── JavBusScraper       (list-page + detail-page parser, dual CSS selectors)
  ├── ImageDownloader     (async cover/screenshot/avatar downloads)
  ├── AutoHealer          (concurrency/delay/fallback adaption on errors)
  └── ProgressReporter    (periodic progress.json every 180s)
```

### Database

SQLite with SQLAlchemy 2.0. `NullPool` for threading safety. `SessionLocal` per operation.

Key tables: `movies`, `actresses`, `genres`, `screenshots`, `magnets`, `sources`, `discovery_queue`.
Many-to-many: `movie_actress`, `movie_genre`.

### Configuration

`jav_meta.config.settings` (Pydantic BaseSettings) loads from `.env`. Two config groups:
- **Discovery phase**: `DISCOVERY_CONCURRENCY`, `DISCOVERY_REQUEST_DELAY`, `DISCOVERY_MAX_RETRIES`, `DISCOVERY_RESUME_WINDOW_HOURS`, `DISCOVERY_EMPTY_PAGE_THRESHOLD`
- **Download phase**: `CONCURRENCY`, `REQUEST_DELAY`, `MAX_RETRIES`, `RETRY_DELAYS`, `PROGRESS_INTERVAL_SECONDS`, `DUMP_INTERVAL_ITEMS`

## Critical Constraints

**Never run `taskkill /F /IM python.exe`.** Use `taskkill /PID <pid>` only after identifying the specific PID.

**Code and documentation must stay in sync.** After any code change, verify `jav_meta/README.md`.

## Agent Discipline

Any file modification must be committed immediately. Before submitting, verify:
1. No deprecated APIs (`datetime.utcnow()`, `imp`, `asyncore`)
2. Concurrency safety (Semaphore/Lock changes reviewed)
3. All imports at file top (no runtime `__import__`)
4. Resource cleanup (httpx clients, DB sessions, file handles)
5. Type/interface consistency across call sites
6. README sync for command/structure changes

Test closure: develop → test → fix → verify.

| Change Scope | Verification |
|---|---|
| Crawler / parser / DB | `uv run python tests/test_crawl_smoke.py` |
| General / quick check | `uv run python tests/test_quick.py` |
| Local file organizer | Manual: `uv run javdb scrape <test-dir>` |
| CLI / config | Manual: `uv run javdb <command>` |

## Security

`JAVBUS_COOKIE` in `.env` is sensitive. `.env`, `data/db.sqlite3`, `data/images/`, `data/progress.json`, `data/discovery_checkpoint.json` are `.gitignore`d.
