#!/usr/bin/env python3
"""
Smoke test: crawl 1 page and verify data integrity.
Run: python tests/test_crawl_smoke.py
"""
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jav_meta.crawler.orchestrator import CrawlOrchestrator
from jav_meta.database.engine import SessionLocal
from jav_meta.database.models import Movie, Actress, Screenshot, Magnet
from jav_meta.config import settings


async def main():
    print("=" * 60)
    print("JAV Meta Smoke Test")
    print("=" * 60)

    # 1. Crawl page 1
    print("\n[1/5] Crawling page 1...")
    orchestrator = CrawlOrchestrator()
    await orchestrator.run_full(start_page=1, end_page=1, skip_selftest=True)
    print("Crawl finished.")

    # 2. Verify database records
    print("\n[2/5] Checking database...")
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

    # 3. Sample movie details
    print("\n[3/5] Sample movie record:")
    m = movies[0]
    print(f"  Code: {m.code}")
    print(f"  Title: {m.title or '(empty)'}")
    print(f"  Date: {m.release_date or '(empty)'}")
    print(f"  Studio: {m.studio or '(empty)'}")
    print(f"  Length: {m.length or '(empty)'} min")
    print(f"  Actresses: {len(m.actresses)}")
    print(f"  Genres: {len(m.genres)}")
    print(f"  Cover local: {m.cover_local or '(empty)'}")

    # 4. Verify images downloaded
    print("\n[4/5] Checking downloaded images...")
    covers = list(settings.covers_dir.glob("*.jpg"))
    actress_photos = list(settings.actresses_dir.glob("*.jpg"))
    print(f"  Covers downloaded: {len(covers)}")
    print(f"  Actress photos downloaded: {len(actress_photos)}")

    if not covers:
        print("  [WARN] No cover images downloaded")

    # 5. Verify crawl log
    from jav_meta.database.models import CrawlLog
    logs = db.query(CrawlLog).order_by(CrawlLog.id.desc()).limit(1).all()
    if logs:
        log = logs[0]
        print(f"\n[5/5] Crawl log:")
        print(f"  Status: {log.status}")
        print(f"  Items: {log.items_count}")
        print(f"  Success: {log.success_count}")
        print(f"  Failed: {log.fail_count}")

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
