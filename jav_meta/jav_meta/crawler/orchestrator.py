import asyncio
import datetime
import time
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

from jav_meta.config import settings
from jav_meta.database.engine import SessionLocal
from jav_meta.database.models import (
    Movie, Actress, Genre, MovieActress, MovieGenre, Screenshot, Magnet,
    CrawlLog, CrawlStatus, ScreenshotType,
)
from jav_meta.scrapers.base import BaseScraper, MovieData
from jav_meta.scrapers.javbus import JavBusScraper
from jav_meta.utils.autoheal import AutoHealer
from jav_meta.utils.http_client import AdaptiveClient
from jav_meta.utils.image_downloader import ImageDownloader
from jav_meta.utils.selftest import SelfTestRunner


class ProgressReporter:
    def __init__(self, interval: int = 180):
        self.interval = interval
        self.started_at = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        self.total_tasks = 0
        self.completed_tasks = 0
        self.failed_tasks = 0
        self.downloaded_images = 0
        self.current_task = "idle"
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    def update(self, total: int = None, completed: int = None, failed: int = None,
               images: int = None, current: str = None):
        if total is not None:
            self.total_tasks = total
        if completed is not None:
            self.completed_tasks = completed
        if failed is not None:
            self.failed_tasks = failed
        if images is not None:
            self.downloaded_images = images
        if current is not None:
            self.current_task = current

    async def _run(self):
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                self._print()

    def _print(self):
        elapsed = (datetime.datetime.utcnow() - self.started_at).total_seconds()
        total = self.total_tasks or 1
        completed = self.completed_tasks
        pct = completed / total * 100 if total > 0 else 0
        avg = elapsed / completed if completed > 0 else 0
        remaining = (total - completed) * avg if completed > 0 else 0
        logger.info(
            f"[Progress] {completed}/{total} ({pct:.1f}%) | "
            f"Failed: {self.failed_tasks} | Images: {self.downloaded_images} | "
            f"Elapsed: {self._fmt(elapsed)} | ETA: {self._fmt(remaining)} | "
            f"Current: {self.current_task}"
        )

    @staticmethod
    def _fmt(seconds: float) -> str:
        return str(datetime.timedelta(seconds=int(seconds)))

    def start(self):
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._stop_event.set()
        if self._task:
            await self._task
        self._print()


class CrawlOrchestrator:
    def __init__(self, scraper: BaseScraper = None, client: AdaptiveClient = None):
        self.client = client or AdaptiveClient()
        self.scraper = scraper or JavBusScraper(self.client)
        self.downloader = ImageDownloader(self.client)
        self.reporter = ProgressReporter(interval=settings.progress_interval_seconds)
        self._running = False
        self.autohealer = AutoHealer(
            on_reduce_concurrency=self._on_reduce_concurrency,
            on_increase_delay=self._on_increase_delay,
            on_switch_fallback=self._on_switch_fallback,
        )

    def _on_reduce_concurrency(self, new_val: int):
        self.client.semaphore = asyncio.Semaphore(new_val)
        self.client.current_concurrency = new_val

    def _on_increase_delay(self, new_val: float):
        settings.request_delay = new_val

    def _on_switch_fallback(self):
        if isinstance(self.scraper, JavBusScraper):
            self.scraper.use_fallback()

    async def run_full(self, start_page: int = 1, end_page: Optional[int] = None,
                       uncensored: bool = False, skip_selftest: bool = False):
        await self._run_crawl(
            crawl_type="full",
            start_page=start_page,
            end_page=end_page,
            uncensored=uncensored,
            skip_selftest=skip_selftest,
        )

    async def run_incremental(self, uncensored: bool = False, skip_selftest: bool = False):
        # Determine last date from existing movies
        with SessionLocal() as db:
            last_movie = db.query(Movie).filter(
                Movie.source == "javbus",
                Movie.status == "active",
            ).order_by(Movie.release_date.desc()).first()
            checkpoint_date = last_movie.release_date if last_movie else None
        logger.info(f"Incremental crawl starting. Last date boundary: {checkpoint_date}")
        await self._run_crawl(
            crawl_type="incremental",
            start_page=1,
            end_page=None,
            uncensored=uncensored,
            checkpoint_date=checkpoint_date,
            skip_selftest=skip_selftest,
        )

    async def _run_crawl(self, crawl_type: str, start_page: int, end_page: Optional[int],
                         uncensored: bool, checkpoint_date: Optional[datetime.date] = None,
                         skip_selftest: bool = False):
        if not skip_selftest:
            SelfTestRunner().assert_ready()

        async with self.client:
            self.scraper.client = self.client
            self._running = True
            self.reporter.start()

            with SessionLocal() as db:
                log = CrawlLog(
                    crawl_type=crawl_type,
                    page_from=start_page,
                    page_to=end_page,
                    status=CrawlStatus.RUNNING,
                    checkpoint_last_date=checkpoint_date,
                )
                db.add(log)
                db.commit()
                log_id = log.id

            page = start_page
            total_fetched = 0
            success_count = 0
            fail_count = 0
            image_count = 0
            checkpoint_page = start_page

            try:
                while self._running:
                    if end_page is not None and page > end_page:
                        break

                    self.reporter.update(current=f"Fetching page {page}")
                    try:
                        items = await self.scraper.crawl_list_page(page, uncensored=uncensored)
                    except Exception as e:
                        logger.error(f"Failed to fetch list page {page}: {e}")
                        fail_count += 1
                        page += 1
                        continue

                    page_empty = not items
                    page_success = bool(items)
                    self.autohealer.record_page_result(
                        items_count=len(items) if items else 0,
                        success=page_success,
                        error_type="empty" if page_empty else "",
                        response_time=0.0,
                    )

                    if page_empty:
                        logger.info(f"No items on page {page}, assuming end of catalog.")
                        # Autoheal evaluate before deciding to break
                        await self.autohealer.evaluate()
                        if self.autohealer.snapshot.consecutive_empty >= 3:
                            logger.warning("AutoHealer triggered fallback but still empty. Stopping.")
                        break

                    total_fetched += len(items)
                    self.reporter.update(total=total_fetched + (end_page or 9999) * 30)

                    for idx, (code, detail_url, thumb_url) in enumerate(items):
                        if not self._running:
                            break

                        self.reporter.update(current=f"Page {page} item {idx+1}/{len(items)}: {code}")

                        # Skip if already exists and fresh enough (for incremental)
                        with SessionLocal() as db:
                            existing = db.query(Movie).filter(Movie.code == code).first()
                            if existing and crawl_type == "incremental":
                                if checkpoint_date and existing.release_date and existing.release_date <= checkpoint_date:
                                    logger.info(f"Incremental boundary reached at {code} ({existing.release_date})")
                                    self._running = False
                                    break

                        detail_start = time.perf_counter()
                        try:
                            movie_data = await self.scraper.get_movie_detail(code, detail_url)
                            detail_time = time.perf_counter() - detail_start
                            self.autohealer.record_page_result(
                                items_count=1, success=True, response_time=detail_time
                            )
                        except Exception as e:
                            detail_time = time.perf_counter() - detail_start
                            self.autohealer.record_page_result(
                                items_count=0, success=False, error_type="connection",
                                response_time=detail_time
                            )
                            logger.error(f"Failed to fetch detail for {code}: {e}")
                            fail_count += 1
                            continue

                        if not movie_data:
                            fail_count += 1
                            continue

                        # Incremental boundary check by date
                        if crawl_type == "incremental" and checkpoint_date and movie_data.release_date:
                            if movie_data.release_date <= checkpoint_date:
                                logger.info(f"Incremental boundary reached at {code} ({movie_data.release_date})")
                                self._running = False
                                break

                        # Persist
                        try:
                            image_count += await self._persist_movie(movie_data, db_factory=SessionLocal)
                        except Exception as e:
                            logger.error(f"Failed to persist {code}: {e}")
                            fail_count += 1
                            continue

                        success_count += 1
                        self.reporter.update(completed=success_count, failed=fail_count, images=image_count)

                    checkpoint_page = page
                    page += 1

                    # AutoHeal evaluation every page
                    await self.autohealer.evaluate()
                    if page % self.autohealer.snapshot.window_pages == 0:
                        self.autohealer.reset_window()

                    # Periodic SQL dump
                    if page % settings.dump_interval_pages == 0:
                        self._dump_sql()

                    # Update checkpoint
                    with SessionLocal() as db:
                        log = db.query(CrawlLog).filter(CrawlLog.id == log_id).first()
                        if log:
                            log.checkpoint_page = checkpoint_page
                            log.items_count = total_fetched
                            log.success_count = success_count
                            log.fail_count = fail_count
                            db.commit()

            except KeyboardInterrupt:
                logger.warning("Crawl interrupted by user.")
            finally:
                completed_normally = self._running
                self._running = False
                await self.reporter.stop()

                with SessionLocal() as db:
                    log = db.query(CrawlLog).filter(CrawlLog.id == log_id).first()
                    if log:
                        log.status = CrawlStatus.COMPLETED if completed_normally else CrawlStatus.FAILED
                        log.finished_at = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                        log.checkpoint_page = checkpoint_page
                        log.items_count = total_fetched
                        log.success_count = success_count
                        log.fail_count = fail_count
                        db.commit()

                self._dump_sql()
                logger.info(f"Crawl finished. Total: {total_fetched}, Success: {success_count}, Failed: {fail_count}")

    async def _persist_movie(self, data: MovieData, db_factory) -> int:
        """Persist movie data and return number of images downloaded."""
        image_count = 0
        with db_factory() as db:
            # Upsert movie
            movie = db.query(Movie).filter(Movie.code == data.code).first()
            if not movie:
                movie = Movie(code=data.code)
                db.add(movie)

            movie.title = data.title or movie.title
            movie.title_en = data.title_en or movie.title_en
            movie.title_jp = data.title_jp or movie.title_jp
            movie.release_date = data.release_date or movie.release_date
            movie.length = data.length or movie.length
            movie.studio = data.studio or movie.studio
            movie.label = data.label or movie.label
            movie.series = data.series or movie.series
            movie.director = data.director or movie.director
            movie.cover_url = data.cover_url or movie.cover_url
            movie.source = data.source
            movie.status = "active"
            movie.updated_at = datetime.datetime.utcnow()
            db.flush()

            # Download cover
            if data.cover_url and not movie.cover_local:
                local = await self.downloader.download_cover(data.cover_url, data.code)
                if local:
                    movie.cover_local = local
                    image_count += 1

            # Actresses
            for ad in data.actresses:
                actress = db.query(Actress).filter(
                    Actress.name == ad.name,
                    Actress.source == ad.source,
                ).first()
                if not actress:
                    actress = Actress(
                        name=ad.name,
                        name_jp=ad.name_jp,
                        source=ad.source,
                    )
                    db.add(actress)
                    db.flush()
                if ad.avatar_url and not actress.avatar_local:
                    local = await self.downloader.download_actress_avatar(ad.avatar_url, ad.name)
                    if local:
                        actress.avatar_local = local
                        image_count += 1

                # Link
                link = db.query(MovieActress).filter(
                    MovieActress.movie_id == movie.id,
                    MovieActress.actress_id == actress.id,
                ).first()
                if not link:
                    db.add(MovieActress(movie_id=movie.id, actress_id=actress.id))

            # Genres
            for gname in data.genres:
                genre = db.query(Genre).filter(Genre.name == gname).first()
                if not genre:
                    genre = Genre(name=gname)
                    db.add(genre)
                    db.flush()
                link = db.query(MovieGenre).filter(
                    MovieGenre.movie_id == movie.id,
                    MovieGenre.genre_id == genre.id,
                ).first()
                if not link:
                    db.add(MovieGenre(movie_id=movie.id, genre_id=genre.id))

            # Screenshots (concurrent download for speed)
            screenshot_tasks = []
            screenshot_urls = []
            for idx, s_url in enumerate(data.screenshots):
                existing = db.query(Screenshot).filter(
                    Screenshot.movie_id == movie.id,
                    Screenshot.url == s_url,
                ).first()
                if not existing:
                    screenshot_tasks.append(
                        self.downloader.download_screenshot(s_url, data.code, idx + 1)
                    )
                    screenshot_urls.append(s_url)

            if screenshot_tasks:
                screenshot_results = await asyncio.gather(*screenshot_tasks, return_exceptions=True)
                for s_url, local in zip(screenshot_urls, screenshot_results):
                    if isinstance(local, str) and local:
                        db.add(Screenshot(
                            movie_id=movie.id,
                            type=ScreenshotType.SCREENSHOT,
                            url=s_url,
                            local_path=local,
                        ))
                        image_count += 1

            # Magnets
            for md in data.magnets:
                existing = db.query(Magnet).filter(
                    Magnet.movie_id == movie.id,
                    Magnet.link == md.link,
                ).first()
                if not existing:
                    db.add(Magnet(
                        movie_id=movie.id,
                        link=md.link,
                        size=md.size,
                        size_bytes=md.size_bytes,
                        date=md.date,
                        source=data.source,
                    ))

            db.commit()
        return image_count

    @staticmethod
    def _dump_sql():
        import sqlite3
        dump_file = settings.dumps_dir / f"dump_{datetime.datetime.utcnow():%Y%m%d_%H%M%S}.sql"
        try:
            settings.dumps_dir.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(settings.db_path))
            with open(dump_file, "w", encoding="utf-8") as f:
                for line in conn.iterdump():
                    f.write(line + "\n")
            conn.close()
            logger.info(f"SQL dump saved to {dump_file}")
        except Exception as e:
            logger.error(f"SQL dump failed: {e}")

    def stop(self):
        self._running = False
