# JAV Meta

Local JAV metadata database and scraper.

## Setup

```bash
pip install -e "."
```

## Usage

```bash
# Initialize database
javdb init

# Full crawl from JavBus
javdb crawl javbus --full

# Incremental update
javdb crawl javbus --incremental

# Scrape local movie folder
javdb scrape /path/to/movies

# Search local database
javdb search SSIS-001
```
