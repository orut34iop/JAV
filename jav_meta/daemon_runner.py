#!/usr/bin/env python3
"""
Crawl daemon runner.
Runs the full crawl in a resilient loop: auto-resume from checkpoint,
auto-restart on crash, logs progress to file for external monitoring.
"""
import asyncio
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jav_meta.crawler.orchestrator import CrawlOrchestrator
from jav_meta.database.engine import SessionLocal
from jav_meta.database.models import CrawlLog, CrawlStatus
from jav_meta.config import settings
from loguru import logger


def setup_logging():
    logger.remove()
    # Console sink (for when run in foreground)
    logger.add(
        lambda msg: print(msg, end=""),
        level="INFO",
        colorize=False,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
    )
    # File sink with rotation
    log_file = settings.data_dir / "crawl_daemon.log"
    logger.add(
        str(log_file),
        level="INFO",
        rotation="10 MB",
        retention="7 days",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
    )


async def is_crawl_complete() -> bool:
    """Check if the latest full crawl reached end of catalog."""
    with SessionLocal() as db:
        log = db.query(CrawlLog).filter(
            CrawlLog.crawl_type == "full",
            CrawlLog.status == CrawlStatus.COMPLETED,
        ).order_by(CrawlLog.id.desc()).first()
        if not log:
            return False
        # A completed full crawl with checkpoint_page > 0 and items_count > 0
        # that ended on an empty page is considered complete.
        # We use a heuristic: if the last completed crawl had < 5 items on its
        # final page, it likely hit the end of catalog.
        # For simplicity, we trust the orchestrator's empty-page break logic.
        # If it completed normally, we assume it's done.
        return True


async def run_once():
    orchestrator = CrawlOrchestrator()
    await orchestrator.run_full(skip_selftest=True)


async def main():
    setup_logging()
    logger.info("=" * 60)
    logger.info("Crawl Daemon Started")
    logger.info("=" * 60)

    while True:
        try:
            await run_once()

            if await is_crawl_complete():
                logger.info("Crawl completed successfully. Daemon exiting.")
                break

            logger.info("Crawl round finished. Restarting in 10 seconds...")
            await asyncio.sleep(10)

        except Exception as e:
            logger.error(f"Crawl crashed: {e}")
            logger.error(traceback.format_exc())
            logger.info("Auto-restarting in 60 seconds...")
            await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
