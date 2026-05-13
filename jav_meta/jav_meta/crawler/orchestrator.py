import asyncio
import datetime
import time
from typing import List, Optional, Set

from loguru import logger
from sqlalchemy import func

from jav_meta.config import settings
from jav_meta.database.engine import SessionLocal
from jav_meta.database.models import (
    Movie, Actress, Genre, MovieActress, MovieGenre, Screenshot, Magnet,
    DiscoveryQueue, QueueStatus, ScreenshotType,
)
from jav_meta.scrapers.base import BaseScraper, MovieData
from jav_meta.scrapers.javbus import JavBusScraper
from jav_meta.utils.autoheal import AutoHealer
from jav_meta.utils.http_client import AdaptiveClient
from jav_meta.utils.image_downloader import ImageDownloader
from jav_meta.utils.selftest import SelfTestRunner


class ProgressReporter:
    """Periodic progress reporter. Writes structured progress.json for external monitoring."""

    def __init__(self, interval: int = None):
        self.interval = interval or settings.progress_interval_seconds
        self.started_at = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        self.total_tasks = 0
        self.completed_tasks = 0
        self.failed_tasks = 0
        self.downloaded_images = 0
        self.current_task = "idle"
        self._phase = ""
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    def set_phase(self, phase: str):
        self._phase = phase

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
        elapsed = (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - self.started_at).total_seconds()
        total = self.total_tasks or 1
        completed = self.completed_tasks
        pct = completed / total * 100 if total > 0 else 0
        avg = elapsed / completed if completed > 0 else 0
        remaining = (total - completed) * avg if completed > 0 else 0
        logger.info(
            f"[{self._phase}] {completed}/{total} ({pct:.1f}%) | "
            f"Failed: {self.failed_tasks} | Images: {self.downloaded_images} | "
            f"Elapsed: {self._fmt(elapsed)} | ETA: {self._fmt(remaining)} | "
            f"Current: {self.current_task}"
        )
        try:
            import json
            progress_file = settings.data_dir / "progress.json"
            progress_file.write_text(json.dumps({
                "phase": self._phase,
                "completed": completed,
                "total": total,
                "percentage": round(pct, 4),
                "failed": self.failed_tasks,
                "images": self.downloaded_images,
                "elapsed_seconds": int(elapsed),
                "eta_seconds": int(remaining),
                "current_task": self.current_task,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat(),
            }, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _fmt(seconds: float) -> str:
        return str(datetime.timedelta(seconds=int(seconds)))

    def start(self):
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._stop_event.set()
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._print()


class CrawlOrchestrator:
    def __init__(self, scraper: BaseScraper = None, client: AdaptiveClient = None):
        self.client = client or AdaptiveClient()
        self.scraper = scraper or JavBusScraper(self.client)
        self.downloader = ImageDownloader(self.client)
        self.reporter = ProgressReporter()
        self._running = False
        self.autohealer = AutoHealer(
            on_reduce_concurrency=self._on_reduce_concurrency,
            on_increase_delay=self._on_increase_delay,
            on_switch_fallback=self._on_switch_fallback,
        )

    def _on_reduce_concurrency(self, new_val: int):
        self.client.semaphore.set_max(new_val)
        self.client.current_concurrency = new_val

    def _on_increase_delay(self, new_val: float):
        settings.request_delay = new_val

    def _on_switch_fallback(self):
        if isinstance(self.scraper, JavBusScraper):
            self.scraper.use_fallback()

    # ── Phase 1: Discovery ────────────────────────────────────────────

    async def run_discovery(self, uncensored: bool = False, skip_selftest: bool = False):
        """Scan all list pages, extract (code, detail_url), populate discovery_queue."""
        if not skip_selftest:
            SelfTestRunner().assert_ready()

        sections = [False]
        if uncensored:
            sections.append(True)

        for section_idx, is_uncensored in enumerate(sections):
            section_label = "uncensored" if is_uncensored else "regular"
            if section_idx > 0:
                # Clear checkpoint between sections so each starts from page 1
                self._clear_discovery_checkpoint()
            logger.info(f"Discovery: scanning {section_label} section")
            await self._discover_section(is_uncensored=is_uncensored)

    async def _discover_section(self, is_uncensored: bool = False):
        """Scan list pages for one section (regular or uncensored)."""

        # Apply discovery-phase HTTP config
        self.client.semaphore.set_max(settings.discovery_concurrency)
        self.client.current_concurrency = settings.discovery_concurrency
        saved_delay = settings.request_delay
        saved_retries = settings.max_retries
        settings.request_delay = settings.discovery_request_delay
        settings.max_retries = settings.discovery_max_retries

        async with self.client:
            self.scraper.client = self.client
            self._running = True
            self.reporter.set_phase("DISCOVER")
            self.reporter.start()

            checkpoint = self._load_discovery_checkpoint()
            start_page = checkpoint["page"]
            if start_page > 1:
                logger.info(f"Resuming discovery from page {start_page} (checkpoint age: {checkpoint['age_minutes']:.0f}m)")
            else:
                logger.info("Starting full discovery scan")

            page = start_page
            total_discovered = 0
            empty_streak = 0
            last_empty_had_error = False

            try:
                while self._running:
                    self.reporter.update(
                        current=f"Scanning page {page}",
                        total=total_discovered + 1,
                        completed=total_discovered,
                    )

                    page_had_error = False
                    try:
                        items = await self.scraper.crawl_list_page(page, uncensored=is_uncensored)
                    except Exception as e:
                        logger.error(f"Failed to fetch list page {page}: {e}")
                        items = None
                        page_had_error = True

                    if items is None:
                        # Network error — not a true empty page, reset streak
                        empty_streak = 0
                        page += 1
                        continue

                    if not items:
                        # Genuinely empty page (HTTP 200 but no movie-box items)
                        empty_streak += 1
                        last_empty_had_error = page_had_error
                        logger.info(f"Empty page {page} (streak: {empty_streak}/{settings.discovery_empty_page_threshold})")

                        if empty_streak >= settings.discovery_empty_page_threshold and not last_empty_had_error:
                            logger.info("Reached end of catalog (no HTTP errors during empty streak)")
                            break
                    else:
                        empty_streak = 0
                        last_empty_had_error = False
                        codes = self._save_discovered_codes(page, items)
                        total_discovered += len(codes)
                        self.reporter.update(completed=total_discovered)

                    self._save_discovery_checkpoint(page)

                    if page % 50 == 0:
                        self._dump_sql()

                    page += 1

            except KeyboardInterrupt:
                logger.warning("Discovery interrupted by user.")
            finally:
                self._running = False
                await self.reporter.stop()
                self._dump_sql()
                settings.request_delay = saved_delay
                settings.max_retries = saved_retries

            logger.info(f"Discovery finished. Total discovered: {total_discovered} codes across {page - start_page} pages.")

    def _save_discovered_codes(self, page: int, items: List[tuple]) -> List[str]:
        """Save discovered codes to discovery_queue. Returns list of codes saved."""
        codes = []
        seen_this_batch = set()
        with SessionLocal() as db:
            for code, detail_url, _thumb_url in items:
                if code in seen_this_batch:
                    continue
                seen_this_batch.add(code)
                existing = db.query(DiscoveryQueue).filter(DiscoveryQueue.code == code).first()
                if not existing:
                    db.add(DiscoveryQueue(
                        code=code,
                        detail_url=detail_url,
                        source="javbus",
                        status=QueueStatus.PENDING,
                    ))
                    codes.append(code)
            db.commit()
        return codes

    def _load_discovery_checkpoint(self) -> dict:
        """Load discovery checkpoint. Returns {page: int, age_minutes: float}."""
        checkpoint_file = settings.data_dir / "discovery_checkpoint.json"
        try:
            if checkpoint_file.exists():
                import json
                data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
                ts = datetime.datetime.fromisoformat(data["timestamp"])
                age = (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - ts).total_seconds() / 60
                if age < settings.discovery_resume_window_hours * 60:
                    return {"page": data["page"] + 1, "age_minutes": age}
        except Exception:
            pass
        return {"page": 1, "age_minutes": 0}

    def _save_discovery_checkpoint(self, page: int):
        """Save discovery checkpoint atomically for resume safety."""
        checkpoint_file = settings.data_dir / "discovery_checkpoint.json"
        tmp_file = settings.data_dir / "discovery_checkpoint.tmp"
        try:
            import json
            data = json.dumps({
                "page": page,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat(),
            }, ensure_ascii=False)
            tmp_file.write_text(data, encoding="utf-8")
            tmp_file.replace(checkpoint_file)
        except Exception:
            pass

    def _clear_discovery_checkpoint(self):
        """Remove discovery checkpoint so next section starts fresh."""
        checkpoint_file = settings.data_dir / "discovery_checkpoint.json"
        try:
            if checkpoint_file.exists():
                checkpoint_file.unlink()
        except Exception:
            pass

    # ── Phase 2: Download ────────────────────────────────────────────

    async def run_full_download(self, uncensored: bool = False, skip_selftest: bool = False):
        """Download details for ALL pending codes in discovery_queue."""
        await self._download_queue(uncensored=uncensored, skip_selftest=skip_selftest)

    async def run_incremental_download(self, uncensored: bool = False, skip_selftest: bool = False):
        """Download details for pending codes, stopping at date boundary."""
        with SessionLocal() as db:
            last_movie = db.query(Movie).filter(
                Movie.source == "javbus",
                Movie.status == "active",
            ).order_by(Movie.release_date.desc()).first()
            checkpoint_date = last_movie.release_date if last_movie else None
        logger.info(f"Incremental download. Date boundary: {checkpoint_date}")
        await self._download_queue(
            uncensored=uncensored,
            skip_selftest=skip_selftest,
            checkpoint_date=checkpoint_date,
        )

    async def _download_queue(self, uncensored: bool = False, skip_selftest: bool = False,
                              checkpoint_date: Optional[datetime.date] = None):
        """Process all pending items in discovery_queue."""
        if not skip_selftest:
            SelfTestRunner().assert_ready()

        async with self.client:
            self.scraper.client = self.client
            self._running = True
            self.reporter.set_phase("DOWNLOAD")
            self.reporter.start()

            success_count = 0
            fail_count = 0
            image_count = 0

            try:
                # First pass: process pending items
                while self._running:
                    item = self._pop_pending()
                    if not item:
                        break

                    code = item.code
                    detail_url = item.detail_url
                    total_pending = self._count_pending() + 1
                    self.reporter.update(
                        total=total_pending + success_count,
                        completed=success_count,
                        failed=fail_count,
                        images=image_count,
                        current=f"Downloading: {code}",
                    )

                    detail_start = time.perf_counter()
                    movie_data = None
                    for attempt in range(settings.max_retries):
                        try:
                            movie_data = await self.scraper.get_movie_detail(code, detail_url)
                            detail_time = time.perf_counter() - detail_start
                            self.autohealer.record_page_result(
                                items_count=1, success=True, response_time=detail_time
                            )
                            break
                        except Exception as e:
                            if attempt < settings.max_retries - 1:
                                delay = settings.retry_delays[min(attempt, len(settings.retry_delays) - 1)]
                                logger.warning(f"Retry {attempt + 1}/{settings.max_retries} for {code} in {delay}s: {e}")
                                await asyncio.sleep(delay)
                            else:
                                detail_time = time.perf_counter() - detail_start
                                self.autohealer.record_page_result(
                                    items_count=0, success=False, error_type="connection",
                                    response_time=detail_time
                                )
                                logger.error(f"Failed to fetch detail for {code}: {e}")

                    if not movie_data:
                        self._mark_failed(code)
                        fail_count += 1
                        continue

                    # Incremental boundary check
                    if checkpoint_date and movie_data.release_date:
                        if movie_data.release_date <= checkpoint_date:
                            logger.info(f"Incremental boundary at {code} ({movie_data.release_date})")
                            self._running = False
                            break

                    # Persist
                    try:
                        new_images = await self._persist_movie(movie_data, db_factory=SessionLocal)
                        image_count += new_images
                        self._mark_done(code)
                        success_count += 1
                    except Exception as e:
                        logger.error(f"Failed to persist {code}: {e}")
                        self._mark_failed(code)
                        fail_count += 1

                    if success_count % 50 == 0:
                        await self.autohealer.evaluate()

                    if success_count % settings.dump_interval_items == 0:
                        self._dump_sql()

                # Second pass: retry failed items
                if self._running:
                    failed_items = self._pop_failed()
                    if failed_items:
                        logger.info(f"Retrying {len(failed_items)} failed items...")
                        for code, detail_url in failed_items:
                            self.reporter.update(current=f"Retrying: {code}")
                            try:
                                movie_data = await self.scraper.get_movie_detail(code, detail_url)
                                if movie_data:
                                    await self._persist_movie(movie_data, db_factory=SessionLocal)
                                    self._mark_done(code)
                                    success_count += 1
                                else:
                                    fail_count += 1
                            except Exception:
                                fail_count += 1

            except KeyboardInterrupt:
                logger.warning("Download interrupted by user.")
            finally:
                self._running = False
                await self.reporter.stop()
                self._dump_sql()

            logger.info(f"Download finished. Success: {success_count}, Failed: {fail_count}, Images: {image_count}")

    def _pop_pending(self):
        """Get one pending item from the queue."""
        with SessionLocal() as db:
            item = db.query(DiscoveryQueue).filter(
                DiscoveryQueue.status == QueueStatus.PENDING
            ).first()
            if not item:
                return None
            # Detach from session before returning
            code = item.code
            detail_url = item.detail_url
            return type('QueueItem', (), {'code': code, 'detail_url': detail_url})()

    def _count_pending(self) -> int:
        with SessionLocal() as db:
            return db.query(DiscoveryQueue).filter(
                DiscoveryQueue.status == QueueStatus.PENDING
            ).count()

    def _mark_done(self, code: str):
        with SessionLocal() as db:
            item = db.query(DiscoveryQueue).filter(DiscoveryQueue.code == code).first()
            if item:
                movie = db.query(Movie).filter(Movie.code == code).first()
                if movie:
                    item.movie_id = movie.id
                item.status = QueueStatus.DONE
                item.completed_at = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                db.commit()

    def _mark_failed(self, code: str):
        with SessionLocal() as db:
            item = db.query(DiscoveryQueue).filter(DiscoveryQueue.code == code).first()
            if item:
                item.status = QueueStatus.FAILED
                db.commit()

    def _pop_failed(self) -> List[tuple]:
        """Get all failed items for retry, resetting them to pending."""
        with SessionLocal() as db:
            items = db.query(DiscoveryQueue).filter(
                DiscoveryQueue.status == QueueStatus.FAILED
            ).all()
            result = [(item.code, item.detail_url) for item in items]
            for item in items:
                item.status = QueueStatus.PENDING
            db.commit()
            return result

    # ── Combined: discover + download ─────────────────────────────────

    async def run_full(self, uncensored: bool = False, skip_selftest: bool = False):
        """Phase 1 + Phase 2: full discovery followed by full download."""
        await self.run_discovery(uncensored=uncensored, skip_selftest=skip_selftest)
        await self.run_full_download(uncensored=uncensored, skip_selftest=True)

    async def run_incremental(self, uncensored: bool = False, skip_selftest: bool = False):
        """Phase 1 + Phase 2: discovery followed by incremental download."""
        await self.run_discovery(uncensored=uncensored, skip_selftest=skip_selftest)
        await self.run_incremental_download(uncensored=uncensored, skip_selftest=True)

    # ── Shared: persist + SQL dump ────────────────────────────────────

    async def _persist_movie(self, data: MovieData, db_factory) -> int:
        """Persist movie data and return number of images downloaded."""
        image_count = 0
        with db_factory() as db:
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
            movie.updated_at = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            db.flush()

            if data.cover_url and not movie.cover_local:
                local = await self.downloader.download_cover(data.cover_url, data.code)
                if local:
                    movie.cover_local = local
                    image_count += 1

            for ad in data.actresses:
                actress = db.query(Actress).filter(
                    Actress.name == ad.name, Actress.source == ad.source,
                ).first()
                if not actress:
                    actress = Actress(name=ad.name, name_jp=ad.name_jp, source=ad.source)
                    db.add(actress)
                    db.flush()
                if ad.avatar_url and not actress.avatar_local:
                    local = await self.downloader.download_actress_avatar(ad.avatar_url, ad.name)
                    if local:
                        actress.avatar_local = local
                        image_count += 1
                link = db.query(MovieActress).filter(
                    MovieActress.movie_id == movie.id, MovieActress.actress_id == actress.id,
                ).first()
                if not link:
                    db.add(MovieActress(movie_id=movie.id, actress_id=actress.id))

            for gname in data.genres:
                genre = db.query(Genre).filter(Genre.name == gname).first()
                if not genre:
                    genre = Genre(name=gname)
                    db.add(genre)
                    db.flush()
                link = db.query(MovieGenre).filter(
                    MovieGenre.movie_id == movie.id, MovieGenre.genre_id == genre.id,
                ).first()
                if not link:
                    db.add(MovieGenre(movie_id=movie.id, genre_id=genre.id))

            screenshot_tasks = []
            screenshot_urls = []
            for idx, s_url in enumerate(data.screenshots):
                existing = db.query(Screenshot).filter(
                    Screenshot.movie_id == movie.id, Screenshot.url == s_url,
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
                            movie_id=movie.id, type=ScreenshotType.SCREENSHOT,
                            url=s_url, local_path=local,
                        ))
                        image_count += 1

            for md in data.magnets:
                existing = db.query(Magnet).filter(
                    Magnet.movie_id == movie.id, Magnet.link == md.link,
                ).first()
                if not existing:
                    db.add(Magnet(
                        movie_id=movie.id, link=md.link,
                        size=md.size, size_bytes=md.size_bytes,
                        date=md.date, source=data.source,
                    ))

            db.commit()
        return image_count

    @staticmethod
    def _dump_sql():
        import sqlite3
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        dump_file = settings.dumps_dir / f"dump_{now:%Y%m%d_%H%M%S}.sql"
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
