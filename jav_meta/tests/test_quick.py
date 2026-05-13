#!/usr/bin/env python3
"""Quick smoke test: fetch list page + 1 movie detail + image download."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jav_meta.scrapers.javbus import JavBusScraper
from jav_meta.utils.http_client import AdaptiveClient
from jav_meta.utils.image_downloader import ImageDownloader
from jav_meta.config import settings


async def main():
    print("Quick Smoke Test")
    print("=" * 50)

    client = AdaptiveClient()
    scraper = JavBusScraper(client)
    downloader = ImageDownloader(client)

    async with client:
        # 1. List page
        print("\n[1/4] Fetching list page 1...")
        items = await scraper.crawl_list_page(1)
        print(f"  Found {len(items)} items")
        if not items:
            print("  FAIL: no items")
            return 1

        code, detail_url, thumb_url = items[0]
        print(f"  First item: code={code}, url={detail_url}, thumb={thumb_url}")

        # 2. Detail page
        print(f"\n[2/4] Fetching detail for {code}...")
        movie = await scraper.get_movie_detail(code, detail_url)
        if not movie:
            print("  FAIL: no movie data")
            return 1
        print(f"  Title: {movie.title or '(empty)'}")
        print(f"  Date: {movie.release_date or '(empty)'}")
        print(f"  Studio: {movie.studio or '(empty)'}")
        print(f"  Length: {movie.length or '(empty)'} min")
        print(f"  Actresses: {len(movie.actresses)}")
        print(f"  Genres: {len(movie.genres)}")
        print(f"  Screenshots: {len(movie.screenshots)}")
        print(f"  Magnets: {len(movie.magnets)}")
        print(f"  Cover URL: {movie.cover_url or '(empty)'}")

        # 3. Image download test
        print(f"\n[3/4] Downloading cover image...")
        if movie.cover_url:
            local = await downloader.download_cover(movie.cover_url, code)
            if local:
                full = settings.project_root / local
                print(f"  OK: {full} (size: {full.stat().st_size} bytes)")
            else:
                print(f"  FAIL: could not download cover")
        else:
            print("  SKIP: no cover URL")

        # 4. Test screenshot download (first one only)
        if movie.screenshots:
            print(f"\n[4/4] Downloading first screenshot...")
            local = await downloader.download_screenshot(movie.screenshots[0], code, 1)
            if local:
                full = settings.project_root / local
                print(f"  OK: {full} (size: {full.stat().st_size} bytes)")
            else:
                print(f"  FAIL: could not download screenshot")
        else:
            print("\n[4/4] SKIP: no screenshots")

    print("\n" + "=" * 50)
    print("Quick test completed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
