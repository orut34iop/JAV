# JAV Meta

Local JAV metadata database and scraper.

## Setup

```bash
# Install dependencies and create virtual environment
uv sync

# Run CLI
uv run javdb --help
```

## Usage

```bash
# Initialize database
uv run javdb init

# Full crawl from JavBus
uv run javdb crawl javbus --full

# Incremental update
uv run javdb crawl javbus --incremental

# Scrape local movie folder
uv run javdb scrape /path/to/movies

# Search local database
uv run javdb search SSIS-001

# Run self-test before crawling
uv run javdb selftest
```
