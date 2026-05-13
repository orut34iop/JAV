# Agent Guidance for JAV

## Project Overview

`JAV` is a local JAV (Japanese Adult Video) metadata database and scraper. It crawls movie metadata from online sources (currently JavBus), stores them in a local SQLite database, and can organize local video files by generating Kodi-compatible NFO files, copying cover images, screenshots, and actress photos.

The actual application code lives in the `jav_meta/` subdirectory. The repository root only contains `README.md` and this `AGENTS.md`.

## Technology Stack

- **Language**: Python >=3.11
- **Environment manager**: UV (mandatory — all scripts run via `uv run`)
- **Build system**: Hatchling (`pyproject.toml`)
- **CLI framework**: Typer
- **HTTP client**: httpx (with HTTP/2 support)
- **HTML parsing**: BeautifulSoup4 + lxml
- **ORM**: SQLAlchemy 2.0 (Alembic is listed as a dependency but not actively used yet)
- **Configuration**: Pydantic Settings (loaded from `.env` file)
- **Logging**: loguru
- **Console output**: rich
- **Async file I/O**: aiofiles
- **Image processing**: Pillow

## Project Structure

```
jav_meta/                           # Project root for the Python package
├── pyproject.toml                  # Build config, dependencies, entry point
├── README.md                       # Human-facing quick-start guide
├── .env / .env.example             # Runtime configuration (cookie, paths, proxy)
├── .gitignore                      # Excludes .env, db, images, logs
├── data/                           # Runtime data (SQLite DB, SQL dumps, images)
│   ├── db.sqlite3                  # Local SQLite database
│   ├── dumps/                      # Periodic SQL dumps generated during crawl
│   └── images/
│       ├── covers/                 # Movie cover art
│       ├── posters/                # Movie poster art
│       ├── screenshots/            # Movie sample screenshots
│       └── actresses/              # Actress avatar images
├── jav_meta/                       # Main Python package
│   ├── __init__.py
│   ├── cli.py                      # Typer CLI: init, crawl, scrape, search, selftest, daemon, stats
│   ├── config.py                   # Pydantic-settings configuration (reads .env)
│   ├── crawler/
│   │   └── orchestrator.py         # CrawlOrchestrator: full / incremental crawl logic, progress reporting, auto-healing, SQL dumps
│   ├── database/
│   │   ├── engine.py               # SQLAlchemy engine (SQLite, NullPool), SessionLocal, init_db
│   │   └── models.py               # ORM models: Movie, Actress, Genre, Screenshot, Magnet, CrawlLog, Source
│   ├── scraper/
│   │   └── local_scraper.py        # LocalScraper: organize local video files, generate NFOs, copy images
│   ├── scrapers/
│   │   ├── base.py                 # BaseScraper ABC, MovieData / ActressData / MagnetData dataclasses
│   │   └── javbus.py               # JavBusScraper: list-page + detail-page parser with primary/fallback selector profiles
│   └── utils/
│       ├── http_client.py          # AdaptiveClient: async httpx client with semaphore, retries, adaptive concurrency
│       ├── image_downloader.py     # ImageDownloader: async download covers/posters/screenshots/actress avatars
│       ├── autoheal.py             # AutoHealer: heuristics to reduce concurrency / increase delay / switch fallback parsers
│       ├── code_matcher.py         # Extract JAV codes from filenames, detect video files, detect filename tags
│       ├── nfo_generator.py        # Generate Kodi-compatible XML NFO files
│       └── selftest.py             # SelfTestRunner: pre-flight checks (network, cookie, parser, DB, disk, dirs)
└── tests/
    ├── test_quick.py               # Quick smoke test: 1 list page + 1 detail + image download
    └── test_crawl_smoke.py         # Full smoke test: crawl 1 page and verify DB integrity
```

## Build & Install Commands

```bash
# Install dependencies and create virtual environment (run inside jav_meta/)
uv sync

# Run CLI via uv (automatically uses .venv)
uv run javdb --help
```

## CLI Commands

| Command | Purpose |
|---------|---------|
| `javdb init` | Create directories and initialize SQLite database |
| `javdb crawl javbus --full` | Full crawl from page 1 to end of catalog |
| `javdb crawl javbus --incremental` | Incremental crawl stopping at already-known release dates |
| `javdb crawl javbus --pages 1-100` | Crawl a specific page range |
| `javdb crawl javbus --uncensored` | Crawl uncensored section |
| `javdb scrape /path/to/movies` | Match local video files to DB and generate NFOs / copy images |
| `javdb search SSIS-001` | Search local DB by code or title keyword |
| `javdb selftest` | Run pre-flight self-tests before crawling |
| `javdb daemon --interval 24` | Background daemon that runs incremental crawls periodically |
| `javdb stats` | Show movie / actress counts |

## Testing

There is no formal test framework (pytest is not in dependencies). Tests are standalone scripts:

```bash
# Run from jav_meta/ directory
uv run python tests/test_quick.py        # Quick connectivity + parser + image smoke test
uv run python tests/test_crawl_smoke.py  # Full 1-page crawl + DB verification
```

Both tests insert the project root into `sys.path` manually and are meant to be run as standalone scripts.

## Session & Runtime Safety Constraints

> **这些约束来自实际生产事故的血的教训，任何新会话必须优先遵守。**

### 1. 进程管理（绝对红线）
- **严禁执行 `taskkill /F /IM python.exe`。** 这会无差别杀死系统中所有 Python 进程，包括 Kimi CLI 自身（它也是 `python.exe`），属于自杀行为。
- 如需清理残留进程，**只允许使用 `taskkill /PID <具体PID> /F`**，且执行前必须通过 `Get-Process python` 确认目标 PID 不是当前 Kimi 会话。
- 残留的旧 Python 进程可能仍持有 SQLite 数据库连接未释放，导致后续操作出现 `SQLite database is locked`。遇到此错误时，首先检查并清理残留进程，而不是重启或重试代码。

### 2. 会话恢复预期
- **Kimi CLI 目前没有 `resume` 或恢复会话的命令。**
- 如果 Kimi 异常退出，只能重新运行 `kimi --yolo` 启动新会话。
- 磁盘上的代码修改、数据库、配置文件都不会丢失；**唯一丢失的是对话上下文**，需要向新会话重新交代背景。

---

## Agent Self-Discipline (Meta-Rule)

> **当会话中了解到任何项目约束、用户偏好、运行时教训、或流程规则时，必须第一时间写入本 `AGENTS.md` 文件。**
> 
> 绝不允许"等用户提醒再保存"、"等下次会话再说"、或"先记在脑子里"。没有持久化到磁盘的信息等于不存在。

### 代码修改后必须立即提交
- **任何文件修改（包括代码修复、配置更新、文档变更、约束写入）完成后，必须第一时间执行 `git add` + `git commit` + `git push`。**
- 绝不允许"等所有改动做完再一起提交"、"等用户说再提交"、或"先放着不管"。
- 单个逻辑变更对应一个 commit，commit message 必须清晰描述改动内容。
- 提交前必须确认没有敏感文件（如 `.env`）被意外纳入。

### 代码自检清单（修改前/后必须执行）
专家在修改任何代码前和提交前，必须主动完成以下检查，不得遗漏：

1. **废弃 API 扫描**：检查是否使用了 Python 已废弃的 API（如 `datetime.utcnow()`、`imp` 模块、`asyncore` 等）。
2. **并发安全审查**：任何修改 `asyncio.Semaphore`、`Lock`、`Event` 等同步原语的代码，必须评估运行时替换/调整的安全性。
3. **README 一致性**：修改安装方式、运行命令或项目结构后，必须同步检查 `README.md` 是否仍然准确。
4. **禁止运行时 `__import__`**：所有导入必须在文件顶部完成，绝不允许在函数体内使用 `__import__` 做动态导入。
5. **资源泄漏检查**：创建 `httpx.Client`、数据库连接、文件句柄的代码，必须有对应的关闭/释放逻辑。
6. **类型与接口一致性**：修改函数签名或模型字段后，检查所有调用点和 ORM 映射是否同步更新。

### 专家基本纪律（Software Engineering Fundamentals）
以下规则不需要用户提醒，是专家自带的本能：

1. **安全第一**
   - 主动扫描代码中是否硬编码了敏感信息（token、cookie、密码、密钥）。
   - 主动确认 `.gitignore` 的完整性，确保 `.env`、数据库、运行时数据、虚拟环境不会被提交。
   - 发现安全隐患必须立即报告并修复，不得隐瞒。

2. **修改前尽职调查**
   - 修改任何代码前，必须先读取并理解相关文件及其依赖关系。
   - 修改后，检查是否破坏了现有接口、配置或功能。
   - 如果修改了公共接口，检查并更新所有调用点。

3. **测试即信仰**
   - 代码改动后，主动运行相关测试，绝不等待用户催促。
   - 即使没有现成的测试，也要通过手动验证（运行 CLI、检查输出）确认改动正确。
   - 测试失败时，当场修复并重新验证，绝不提交未修复的代码。

4. **状态感知**
   - 每次会话开始时，主动执行 `git status` 确认项目状态。
   - 主动检查环境是否正常（`.venv` 是否存在、依赖是否完整、`.env` 是否配置）。
   - 发现未提交的改动、未跟踪的文件、或环境异常，必须立即处理或报告。

5. **沟通纪律**
   - 回复简洁，直击要点，不绕圈子，不自我辩解。
   - 发现风险、隐患、或不确定的事项，必须主动报告，不得假设用户已知。
   - 报告问题时，必须同时给出解决方案或选项，不能只抛问题。

6. **最小化改动**
   - 只做解决当前问题所必需的修改，绝不引入无关变更。
   - 不删除用户未要求删除的文件，不重构用户未要求重构的代码。
   - 保持向后兼容，除非用户明确要求 breaking change。

---

## Development & Testing Closure Policy

> **核心纪律：任何代码修改必须完成 开发 → 测试 → 修复 → 验证 的闭环，禁止未测试直接提交。**

### 1. 开发阶段
- 修改前必须确认已理解需求与现有代码逻辑。
- 坚持最小化改动，绝不引入无关变更。
- 遵循现有代码风格（英文注释、类型提示、异步 I/O 规范等）。

### 2. 测试阶段
- 所有改动必须通过相关冒烟测试，根据改动范围选择：
  - 爬虫 / 解析器 / 数据库相关改动 → `uv run python tests/test_crawl_smoke.py`
  - 通用改动或快速验证 → `uv run python tests/test_quick.py`
  - 本地文件整理相关改动 → 手动运行 `uv run javdb scrape <测试目录>` 验证
  - CLI / 配置相关改动 → 手动验证 `uv run javdb <相关命令>` 正常执行
- 新增功能必须补充验证步骤并在回复中说明。

### 3. 修复阶段
- **测试失败必须当场修复，绝不允许"先提交再说"。**
- 修复后必须重新运行对应测试，直到通过为止。
- 若遇到无法当场解决的阻塞问题，必须显式向用户报告并征得同意后才能暂停。

### 4. 闭环确认
- 测试全部通过后，向用户明确报告：改了什么、测了什么、结果如何。
- 如有已知限制或后续 TODO，必须显式列出，不得隐瞒。

---

## Code Style & Conventions

- **Language**: All code and comments are written in English.
- **Type hints**: Used throughout (`typing.Optional`, `List`, etc.).
- **Async I/O**: All network operations use `async`/`await`. Database writes inside the crawler happen in synchronous SQLAlchemy sessions (SQLite).
- **Settings**: All tunables live in `jav_meta.config.settings` (a Pydantic `BaseSettings` instance). Do not hard-code URLs, paths, or timeouts.
- **Database sessions**: `SessionLocal` is instantiated per operation. The engine uses `NullPool` to avoid SQLite threading issues.
- **Image paths**: Stored as relative POSIX paths (forward slashes) against `settings.project_root`.
- **Safe filenames**: `ImageDownloader._safe_filename` strips Windows-illegal characters and truncates to 200 chars.

## Current System State

> 新会话接手时必须知晓的当前状态，避免重复初始化或误操作。

- **数据库**: `data/db.sqlite3` 已存在且包含真实爬取数据（约 53 部电影、13 位演员、424 张截图）。**禁止随意删除或重置**，除非用户明确命令。
- **图片资产**: `data/images/` 下已有真实下载的封面、演员头像、截图，均为生产数据。
- **验证状态**: 冒烟测试（`test_quick.py` + `test_crawl_smoke.py`）已于 2026-05-13 通过，系统当前工作正常。
- **SQL 备份**: `data/dumps/` 下已有两次自动导出的 SQL dump，可作为回滚参考。
- **环境**: `jav_meta/.venv` 已初始化，`uv sync` 已完成，依赖就绪。

## Known Issues & Technical Debt

> 当前代码中已确认但尚未修复的严重问题和新会话接手时必须知晓的风险。

1. ~~http_client.py — 运行时替换 `asyncio.Semaphore`（严重并发安全 bug）~~ ✅ **已修复**
   - 已实现 `AdaptiveSemaphore` 类，通过 `asyncio.Condition` 安全支持动态上限调整，彻底消除死锁风险。

2. ~~models.py — 大量使用已废弃的 `datetime.datetime.utcnow()`~~ ✅ **已修复**
   - 已统一替换为 `_utc_now()` 工厂函数，返回与 `utcnow()` 格式兼容的 naive UTC datetime。

---

## Key Runtime Behaviors

1. **Crawl Orchestrator** (`CrawlOrchestrator`):
   - Supports full, incremental, and page-range crawls.
   - Tracks progress every 180 seconds (configurable via `PROGRESS_INTERVAL_SECONDS`).
   - Dumps SQLite to SQL every 50 pages (`DUMP_INTERVAL_PAGES`).
   - Persists crawl state in `CrawlLog` with checkpoint support.

2. **Adaptive HTTP Client** (`AdaptiveClient`):
   - Uses an `asyncio.Semaphore` for concurrency control.
   - Automatically reduces concurrency on HTTP 429/502/503/504 or connection errors.
   - Automatically increases concurrency when requests succeed on first attempt.
   - Retries up to 3 times with configurable delays.

3. **AutoHealer** (`AutoHealer`):
   - Evaluated after every page during crawl.
   - Triggers:
     - `>=5` consecutive failures → reduce concurrency.
     - `<30%` success rate over last window → reduce concurrency.
     - `>=3` consecutive empty pages → switch JavBus scraper to fallback CSS selectors.
     - `>=3` connection errors → increase request delay.

4. **JavBus Scraper** (`JavBusScraper`):
   - Two selector profiles: `primary` and `fallback`.
   - Extracts movie code with regex from both page text and URL path.
   - Parses detail page info lines by matching Chinese header text (e.g. `日期`, `長度`, `製作商`).
   - Magnet links are parsed from a table on the detail page.

5. **Local File Organizer** (`LocalScraper`):
   - Groups video files by extracted JAV code.
   - Generates Kodi-compatible `.nfo` files.
   - Copies covers → `{code}-fanart.jpg`, posters → `{code}-poster.jpg`.
   - Copies screenshots → `extrafanart/` directory.
   - Copies actress avatars → `.actors/` directory.
   - Supports dry-run, renaming, and per-movie folder creation.

## Configuration via `.env`

Copy `.env.example` to `.env` and fill in at minimum:

```env
JAVBUS_COOKIE=existmag=all; __cfduid=xxx; ...
```

Other important options:
- `JAVBUS_BASE_URL` / `JAVBUS_UNCENSORED_URL` — JavBus domain.
- `CONCURRENCY` / `ADAPTIVE_CONCURRENCY` / `REQUEST_TIMEOUT` / `MAX_RETRIES` — Crawler tuning.
- `PROXY_URL` — Optional HTTP proxy.
- `DUMP_INTERVAL_PAGES` — How often to export SQLite dump.

## Database Schema (SQLite)

Key tables: `movies`, `actresses`, `genres`, `screenshots`, `magnets`, `sources`, `crawl_logs`.

Many-to-many join tables: `movie_actress`, `movie_genre`.

All tables have `created_at` / `updated_at` timestamps. Movies have a `status` field (`active`/`missing`/`deprecated`).

## Security Considerations

- **Sensitive data**: `JAVBUS_COOKIE` in `.env` contains session cookies. `.env` is `.gitignore`d.
- **Database**: `data/db.sqlite3` and `data/db.sqlite3-journal` are `.gitignore`d.
- **Runtime images**: `data/images/` contents are `.gitignore`d; only `.gitkeep` files are tracked to preserve directory structure.
- **Proxy**: Optional proxy can be configured via `PROXY_URL` for regions where JavBus is blocked.
- **No secrets scanning**: No automated secret scanning is configured yet.
- **Process safety**: See "Session & Runtime Safety Constraints" above. Never run mass-kill commands against `python.exe`.

## Git Info

- Remote origin: `https://github.com/orut34iop/JAV.git`
- Default branch: `master`
