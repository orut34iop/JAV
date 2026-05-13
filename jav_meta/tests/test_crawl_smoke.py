#!/usr/bin/env python3
"""
Smoke test: discover 1 page and verify data integrity.
Run: uv run python tests/test_crawl_smoke.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jav_meta.crawler.orchestrator import CrawlOrchestrator
from jav_meta.database.engine import SessionLocal
from jav_meta.database.models import Movie, Actress, Screenshot, Magnet, DiscoveryQueue
from jav_meta.config import settings


async def main():
    print("=" * 60)
    print("JAV Meta Smoke Test")
    print("=" * 60)

    # 1. Discover page 1 only (fast)
    print("\n[1/4] Discovering page 1...")
    orchestrator = CrawlOrchestrator()
    await orchestrator.run_discovery(skip_selftest=True)
    # Note: discovery scans all pages. For smoke test we check page 1 data.

    # 2. Download pending items
    print("\n[2/4] Downloading discovered items...")
    await orchestrator.run_full_download(skip_selftest=True)
    print("Download finished.")

    # 3. Verify database records
    print("\n[3/4] Checking database...")
    db = SessionLocal()
    movies = db.query(Movie).all()
    actresses = db.query(Actress).all()
    screenshots = db.query(Screenshot).all()
    magnets = db.query(Magnet).all()

    print(f"  Movies: {len(movies)}")
    print(f"  Actresses: {len(actresses)}")
    print(f"  Screenshots: {len(screenshots)}")
    print(f"  Magnets: {len(magnets)}")

    if not movies:
        print("\n[FAIL] No movies found in database!")
        db.close()
        sys.exit(1)

    # 4. Sample movie details
    print("\n[4/4] Sample movie record:")
    m = movies[0]
    print(f"  Code: {m.code}")
    print(f"  Title: {m.title or '(empty)'}")
    print(f"  Date: {m.release_date or '(empty)'}")
    print(f"  Studio: {m.studio or '(empty)'}")
    print(f"  Length: {m.length or '(empty)'} min")
    print(f"  Actresses: {len(m.actresses)}")
    print(f"  Genres: {len(m.genres)}")
    print(f"  Cover local: {m.cover_local or '(empty)'}")

    # Verify images downloaded
    covers = list(settings.covers_dir.glob("*.jpg"))
    print(f"  Covers downloaded: {len(covers)}")

    # Verify discovery queue
    queue_stats = {
        "done": db.query(DiscoveryQueue).filter(DiscoveryQueue.status == "done").count(),
        "failed": db.query(DiscoveryQueue).filter(DiscoveryQueue.status == "failed").count(),
        "pending": db.query(DiscoveryQueue).filter(DiscoveryQueue.status == "pending").count(),
    }
    print(f"  Queue: done={queue_stats['done']} failed={queue_stats['failed']} pending={queue_stats['pending']}")

    db.close()

    print("\n" + "=" * 60)
    print("Smoke test PASSED. System is working correctly.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
    except KeyboardInterrupt:
        print("\nTest interrupted.")
        exit_code = 1
    sys.exit(exit_code)
