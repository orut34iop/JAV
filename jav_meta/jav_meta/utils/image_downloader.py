import hashlib
import pathlib
import re
from typing import Optional

import aiofiles
import httpx
from loguru import logger

from jav_meta.config import settings
from jav_meta.utils.http_client import AdaptiveClient


class ImageDownloader:
    def __init__(self, client: AdaptiveClient):
        self.client = client
        # Dedicated fast client for images: short timeout, no retries
        self._img_client: Optional[httpx.AsyncClient] = None

    async def _get_img_client(self) -> httpx.AsyncClient:
        if self._img_client is None:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.javbus.com/",
                "Cookie": settings.javbus_cookie,
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
            }
            client_kwargs = dict(headers=headers, timeout=httpx.Timeout(10.0), follow_redirects=True)
            if settings.proxy_url:
                client_kwargs["proxy"] = settings.proxy_url
            self._img_client = httpx.AsyncClient(**client_kwargs)
        return self._img_client

    def _resolve_url(self, url: str) -> str:
        """Convert relative image URLs to absolute URLs."""
        if not url:
            return url
        if url.startswith("http://") or url.startswith("https://"):
            return url
        if url.startswith("//"):
            return "https:" + url
        if url.startswith("/"):
            return settings.javbus_base_url.rstrip("/") + url
        return url

    def _safe_filename(self, name: str) -> str:
        """Remove Windows path separators and other illegal chars from filename."""
        name = name.replace("\\", "_").replace("/", "_")
        name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name)
        if len(name) > 200:
            name = name[:200]
        return name.strip("._ ")

    async def download(self, url: str, dest_path: pathlib.Path, filename: Optional[str] = None) -> Optional[str]:
        if not url:
            return None
        url = self._resolve_url(url)
        try:
            img_client = await self._get_img_client()
            resp = await img_client.get(url)
            if resp.status_code != 200:
                logger.debug(f"Image download failed {resp.status_code}: {url}")
                return None

            if filename is None:
                ext = pathlib.Path(url.split("?")[0]).suffix or ".jpg"
                filename = hashlib.md5(url.encode()).hexdigest() + ext

            filename = self._safe_filename(filename)
            if not filename:
                filename = "image.jpg"

            dest_path.mkdir(parents=True, exist_ok=True)
            full_path = dest_path / filename

            async with aiofiles.open(full_path, "wb") as f:
                await f.write(resp.content)

            relative = full_path.relative_to(settings.project_root)
            return str(relative).replace("\\", "/")
        except Exception as e:
            logger.debug(f"Failed to download image {url}: {e}")
            return None

    async def download_cover(self, url: str, code: str) -> Optional[str]:
        safe_code = self._safe_filename(code)
        return await self.download(url, settings.covers_dir, f"{safe_code}.jpg")

    async def download_poster(self, url: str, code: str) -> Optional[str]:
        safe_code = self._safe_filename(code)
        return await self.download(url, settings.posters_dir, f"{safe_code}.jpg")

    async def download_screenshot(self, url: str, code: str, index: int) -> Optional[str]:
        safe_code = self._safe_filename(code)
        ext = pathlib.Path(self._resolve_url(url).split("?")[0]).suffix or ".jpg"
        return await self.download(url, settings.screenshots_dir / safe_code, f"{index:02d}{ext}")

    async def download_actress_avatar(self, url: str, name: str) -> Optional[str]:
        safe_name = self._safe_filename(name)
        if not safe_name:
            safe_name = "unknown"
        ext = pathlib.Path(self._resolve_url(url).split("?")[0]).suffix or ".jpg"
        return await self.download(url, settings.actresses_dir, f"{safe_name}{ext}")
