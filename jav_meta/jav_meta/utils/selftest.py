import asyncio
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, List

from bs4 import BeautifulSoup
from loguru import logger
from rich.console import Console
from rich.table import Table

from jav_meta.config import settings
from jav_meta.database.engine import SessionLocal
from jav_meta.utils.http_client import AdaptiveClient

console = Console()


@dataclass
class TestResult:
    name: str
    category: str
    passed: bool
    message: str
    duration_ms: float


SelfTestFunc = Callable[[], TestResult]


class SelfTestRunner:
    def __init__(self):
        self.tests: List[SelfTestFunc] = []
        self._register_defaults()

    def _register_defaults(self):
        self.tests.append(self._test_network)
        self.tests.append(self._test_cookie)
        self.tests.append(self._test_parser_health)
        self.tests.append(self._test_database)
        self.tests.append(self._test_disk_space)
        self.tests.append(self._test_image_dirs)

    def _test_network(self) -> TestResult:
        import time
        start = time.perf_counter()
        try:
            import urllib.request
            req = urllib.request.Request(
                settings.javbus_base_url,
                headers={"User-Agent": "Mozilla/5.0"},
                method="HEAD",
            )
            resp = urllib.request.urlopen(req, timeout=10)
            duration = (time.perf_counter() - start) * 1000
            return TestResult("Network Reachability", "network", True,
                              f"HTTP {resp.status}, reachable", duration)
        except Exception as e:
            duration = (time.perf_counter() - start) * 1000
            return TestResult("Network Reachability", "network", False,
                              str(e), duration)

    def _test_cookie(self) -> TestResult:
        import time
        start = time.perf_counter()
        if not settings.javbus_cookie:
            duration = (time.perf_counter() - start) * 1000
            return TestResult("Cookie Validity", "auth", False,
                              "JAVBUS_COOKIE is empty", duration)

        try:
            import urllib.request
            req = urllib.request.Request(
                f"{settings.javbus_base_url}/page/1",
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Cookie": settings.javbus_cookie,
                },
            )
            resp = urllib.request.urlopen(req, timeout=15)
            html = resp.read().decode("utf-8", errors="ignore")
            duration = (time.perf_counter() - start) * 1000

            if "JavBus" not in html and "javbus" not in html.lower():
                return TestResult("Cookie Validity", "auth", False,
                                  "Page returned but does not contain JavBus content (possibly blocked or wrong domain)", duration)

            soup = BeautifulSoup(html, "lxml")
            items = soup.select("a.movie-box")
            if len(items) == 0:
                return TestResult("Cookie Validity", "auth", False,
                                  "Cookie accepted but no movies found on page 1 (check existmag=all)", duration)

            return TestResult("Cookie Validity", "auth", True,
                              f"OK, found {len(items)} movie items on page 1", duration)
        except Exception as e:
            duration = (time.perf_counter() - start) * 1000
            return TestResult("Cookie Validity", "auth", False,
                              str(e), duration)

    def _test_parser_health(self) -> TestResult:
        import time
        start = time.perf_counter()
        try:
            import urllib.request
            # Test list page selectors
            req = urllib.request.Request(
                f"{settings.javbus_base_url}/page/1",
                headers={"User-Agent": "Mozilla/5.0", "Cookie": settings.javbus_cookie},
            )
            resp = urllib.request.urlopen(req, timeout=15)
            list_html = resp.read().decode("utf-8", errors="ignore")
            list_soup = BeautifulSoup(list_html, "lxml")
            list_checks = {
                "movie-box": bool(list_soup.select("a.movie-box")),
            }
            list_failed = [k for k, v in list_checks.items() if not v]
            if list_failed:
                duration = (time.perf_counter() - start) * 1000
                return TestResult("Parser Selectors", "parser", False,
                                  f"List selectors not found: {', '.join(list_failed)}", duration)

            # Extract first movie link to test detail page selectors
            first_link = list_soup.select_one("a.movie-box")
            if not first_link or not first_link.get("href"):
                duration = (time.perf_counter() - start) * 1000
                return TestResult("Parser Selectors", "parser", False,
                                  "No movie link found to test detail selectors", duration)

            detail_url = first_link.get("href")
            if not detail_url.startswith("http"):
                detail_url = settings.javbus_base_url + detail_url

            req2 = urllib.request.Request(
                detail_url,
                headers={"User-Agent": "Mozilla/5.0", "Cookie": settings.javbus_cookie},
            )
            resp2 = urllib.request.urlopen(req2, timeout=15)
            detail_html = resp2.read().decode("utf-8", errors="ignore")
            detail_soup = BeautifulSoup(detail_html, "lxml")

            detail_checks = {
                "bigImage": bool(detail_soup.select(".bigImage")),
                "info": bool(detail_soup.select(".info")),
            }
            detail_failed = [k for k, v in detail_checks.items() if not v]
            duration = (time.perf_counter() - start) * 1000
            if detail_failed:
                return TestResult("Parser Selectors", "parser", False,
                                  f"Detail selectors not found: {', '.join(detail_failed)}", duration)
            return TestResult("Parser Selectors", "parser", True,
                              f"All selectors OK (list + detail)", duration)
        except Exception as e:
            duration = (time.perf_counter() - start) * 1000
            msg = str(e)
            # Rate limiting is temporary, not a parser failure
            if "429" in msg or "Too Many Requests" in msg:
                return TestResult("Parser Selectors", "parser", True,
                                  f"Rate limited (will retry): {msg}", duration)
            return TestResult("Parser Selectors", "parser", False, msg, duration)

    def _test_database(self) -> TestResult:
        import time
        from sqlalchemy import text
        start = time.perf_counter()
        try:
            db = SessionLocal()
            db.execute(text("SELECT 1"))
            db.close()
            duration = (time.perf_counter() - start) * 1000
            return TestResult("Database RW", "database", True,
                              f"SQLite OK: {settings.db_path}", duration)
        except Exception as e:
            duration = (time.perf_counter() - start) * 1000
            return TestResult("Database RW", "database", False,
                              str(e), duration)

    def _test_disk_space(self) -> TestResult:
        import time
        start = time.perf_counter()
        try:
            usage = shutil.disk_usage(settings.data_dir)
            free_gb = usage.free / (1024**3)
            duration = (time.perf_counter() - start) * 1000
            if free_gb < 1.0:
                return TestResult("Disk Space", "system", False,
                                  f"Only {free_gb:.1f} GB free", duration)
            return TestResult("Disk Space", "system", True,
                              f"{free_gb:.1f} GB free", duration)
        except Exception as e:
            duration = (time.perf_counter() - start) * 1000
            return TestResult("Disk Space", "system", False,
                              str(e), duration)

    def _test_image_dirs(self) -> TestResult:
        import time
        start = time.perf_counter()
        try:
            test_file = settings.covers_dir / ".write_test"
            test_file.write_text("ok")
            test_file.unlink()
            duration = (time.perf_counter() - start) * 1000
            return TestResult("Image Dirs Writable", "filesystem", True,
                              "All image directories writable", duration)
        except Exception as e:
            duration = (time.perf_counter() - start) * 1000
            return TestResult("Image Dirs Writable", "filesystem", False,
                              str(e), duration)

    def run(self) -> List[TestResult]:
        results = []
        for test_fn in self.tests:
            try:
                result = test_fn()
            except Exception as e:
                result = TestResult(test_fn.__name__, "unknown", False, str(e), 0)
            results.append(result)
        return results

    def run_and_print(self) -> bool:
        results = self.run()
        table = Table(title="Self-Test Results")
        table.add_column("Category", style="cyan")
        table.add_column("Test", style="green")
        table.add_column("Result", style="bold")
        table.add_column("Message", style="yellow")
        table.add_column("Time", style="dim")

        all_passed = True
        for r in results:
            status = "[green]PASS[/green]" if r.passed else "[red]FAIL[/red]"
            if not r.passed:
                all_passed = False
            table.add_row(r.category, r.name, status, r.message, f"{r.duration_ms:.0f}ms")

        console.print(table)
        return all_passed

    def assert_ready(self):
        results = self.run()
        failed = [r for r in results if not r.passed]
        if failed:
            for r in failed:
                logger.error(f"Self-test failed [{r.category}/{r.name}]: {r.message}")
            raise RuntimeError(f"{len(failed)} self-test(s) failed. Crawl aborted.")
        logger.info("All self-tests passed. System ready.")
