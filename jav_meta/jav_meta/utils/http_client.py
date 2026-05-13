import asyncio
import random
from typing import Optional

import httpx
from loguru import logger

from jav_meta.config import settings


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]


class AdaptiveSemaphore:
    """Asyncio-compatible semaphore that supports safe dynamic limit adjustment.

    Unlike asyncio.Semaphore, the limit can be changed at runtime without
    leaving tasks waiting on a discarded semaphore object (which causes deadlocks).
    """

    def __init__(self, initial: int):
        self._max = initial
        self._current = 0
        self._lock = asyncio.Lock()
        self._condition = asyncio.Condition(self._lock)

    def set_max(self, value: int):
        self._max = max(1, value)
        if self._current < self._max:
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon(self._notify_sync)
            except RuntimeError:
                pass

    def _notify_sync(self):
        try:
            asyncio.create_task(self._notify_async())
        except RuntimeError:
            pass

    async def _notify_async(self):
        async with self._lock:
            self._condition.notify_all()

    async def acquire(self):
        async with self._lock:
            while self._current >= self._max:
                await self._condition.wait()
            self._current += 1

    async def release(self):
        async with self._lock:
            self._current -= 1
            self._condition.notify()

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.release()


class AdaptiveClient:
    def __init__(
        self,
        concurrency: int = None,
        adaptive: bool = None,
        timeout: float = None,
        max_retries: int = None,
        retry_delays: list = None,
        cookie: str = None,
        proxy: Optional[str] = None,
    ):
        self.concurrency = concurrency or settings.concurrency
        self.adaptive = adaptive if adaptive is not None else settings.adaptive_concurrency
        self.timeout = timeout or settings.request_timeout
        self.max_retries = max_retries or settings.max_retries
        self.retry_delays = retry_delays or settings.retry_delays
        self.cookie = cookie or settings.javbus_cookie
        self.proxy = proxy or settings.proxy_url

        self.semaphore = AdaptiveSemaphore(self.concurrency)
        self.current_concurrency = self.concurrency
        self.client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7",
            "Cookie": self.cookie,
            "Referer": "https://www.javbus.com/",
        }
        client_kwargs = dict(
            headers=headers,
            timeout=httpx.Timeout(self.timeout),
            http2=True,
            follow_redirects=True,
        )
        if self.proxy:
            client_kwargs["proxy"] = self.proxy
        self.client = httpx.AsyncClient(**client_kwargs)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.aclose()

    def _get_retry_delay(self, attempt: int) -> float:
        if attempt < len(self.retry_delays):
            return self.retry_delays[attempt]
        return self.retry_delays[-1]

    def _adapt_down(self):
        if not self.adaptive or self.current_concurrency <= 1:
            return
        old = self.current_concurrency
        self.current_concurrency = max(1, old - 2)
        if old != self.current_concurrency:
            self.semaphore.set_max(self.current_concurrency)
            logger.warning(f"Adaptive concurrency reduced: {old} -> {self.current_concurrency}")

    def _adapt_up(self):
        if not self.adaptive or self.current_concurrency >= self.concurrency:
            return
        old = self.current_concurrency
        self.current_concurrency = min(self.concurrency, old + 1)
        if old != self.current_concurrency:
            self.semaphore.set_max(self.current_concurrency)
            logger.info(f"Adaptive concurrency increased: {old} -> {self.current_concurrency}")

    async def get(self, url: str, **kwargs) -> httpx.Response:
        async with self.semaphore:
            last_exception = None
            for attempt in range(self.max_retries):
                try:
                    await asyncio.sleep(settings.request_delay)
                    resp = await self.client.get(url, **kwargs)
                    if resp.status_code == 200:
                        if self.adaptive and attempt == 0:
                            self._adapt_up()
                        return resp
                    if resp.status_code in (429, 503, 502, 504):
                        logger.warning(f"Rate limited / server error {resp.status_code} for {url}, adapting down...")
                        self._adapt_down()
                        delay = self._get_retry_delay(attempt)
                        logger.info(f"Retrying in {delay}s (attempt {attempt + 1}/{self.max_retries})")
                        await asyncio.sleep(delay)
                        continue
                    resp.raise_for_status()
                    return resp
                except httpx.HTTPStatusError as e:
                    last_exception = e
                    if e.response.status_code >= 500:
                        self._adapt_down()
                    delay = self._get_retry_delay(attempt)
                    logger.warning(f"HTTP error for {url}: {e}, retrying in {delay}s")
                    await asyncio.sleep(delay)
                except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
                    last_exception = e
                    delay = self._get_retry_delay(attempt)
                    logger.warning(f"Connection error for {url}: {e}, retrying in {delay}s")
                    await asyncio.sleep(delay)
            raise last_exception or Exception(f"Failed to fetch {url} after {self.max_retries} retries")

    async def get_text(self, url: str, **kwargs) -> str:
        resp = await self.get(url, **kwargs)
        return resp.text
