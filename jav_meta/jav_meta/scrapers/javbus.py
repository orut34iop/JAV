import re
from datetime import date
from typing import List, Optional

from bs4 import BeautifulSoup
from loguru import logger

from jav_meta.config import settings
from jav_meta.scrapers.base import BaseScraper, MovieData, ActressData, MagnetData
from jav_meta.utils.http_client import AdaptiveClient


SELECTOR_PROFILES = {
    "primary": {
        "list_movie_box": "a.movie-box",
        "list_thumb_img": ".photo-frame img",
        "list_info": ".photo-info",
        "detail_title": ".container h3",
        "detail_big_image": ".bigImage img",
        "detail_info_p": ".info p",
        "detail_actress_box": "#avatar-waterfall .avatar-box",
        "detail_actress_img": "img",
        "detail_actress_name": "span",
        "detail_genre": ".genre a",
        "detail_sample": ".sample-box",
        "detail_magnet_row": "#magnet-table tr",
    },
    "fallback": {
        "list_movie_box": "a.movie-box",
        "list_thumb_img": "img",
        "list_info": ".photo-info",
        "detail_title": "h3",
        "detail_big_image": ".bigImage img",
        "detail_info_p": ".info p",
        "detail_actress_box": ".avatar-box",
        "detail_actress_img": "img",
        "detail_actress_name": "span",
        "detail_genre": "a[href*='/genre/']",
        "detail_sample": ".sample-box",
        "detail_magnet_row": "tr:has(a[href^='magnet:'])",
    },
}


class JavBusScraper(BaseScraper):
    name = "javbus"
    priority = 10

    def __init__(self, client: AdaptiveClient = None):
        self.client = client
        self._profile_name = "primary"
        self._sel = SELECTOR_PROFILES["primary"]

    def use_fallback(self):
        if self._profile_name != "fallback":
            self._profile_name = "fallback"
            self._sel = SELECTOR_PROFILES["fallback"]
            logger.warning("[JavBusScraper] Switched to FALLBACK selector profile.")

    def use_primary(self):
        self._profile_name = "primary"
        self._sel = SELECTOR_PROFILES["primary"]

    def _base_url(self, uncensored: bool = False) -> str:
        if uncensored:
            return settings.javbus_uncensored_url
        return settings.javbus_base_url

    async def crawl_list_page(self, page: int, uncensored: bool = False) -> List[tuple]:
        base = self._base_url(uncensored)
        url = f"{base}/page/{page}"
        html = await self.client.get_text(url)
        soup = BeautifulSoup(html, "lxml")

        results = []
        boxes = soup.select(self._sel["list_movie_box"])
        for box in boxes:
            href = box.get("href", "")
            if not href:
                continue
            detail_url = href if href.startswith("http") else f"{settings.javbus_base_url}{href}"

            img = box.select_one(self._sel["list_thumb_img"])
            thumb_url = img.get("src", "") if img else ""

            code = ""
            info = box.select_one(self._sel["list_info"])
            if info:
                spans = info.find_all("span")
                if spans:
                    raw = spans[0].get_text(strip=True)
                    # Extract only the code pattern from the span text
                    m = re.search(r"([A-Za-z]{2,8}-\d{2,5})", raw)
                    if m:
                        code = m.group(1).upper()
            if not code:
                m = re.search(r"/([A-Za-z]+-\d+)(?:_?[A-Za-z])?$", href)
                if m:
                    code = m.group(1).upper()
            if code:
                results.append((code, detail_url, thumb_url))
        return results

    async def get_movie_detail(self, code: str, detail_url: str) -> Optional[MovieData]:
        try:
            html = await self.client.get_text(detail_url)
        except Exception as e:
            logger.error(f"Failed to fetch detail for {code}: {e}")
            return None

        soup = BeautifulSoup(html, "lxml")
        movie = MovieData(code=code, source="javbus")

        title_tag = soup.select_one(self._sel["detail_title"])
        if title_tag:
            movie.title = title_tag.get_text(strip=True)

        big_image = soup.select_one(self._sel["detail_big_image"])
        if big_image:
            movie.cover_url = big_image.get("src", "")

        info_p_list = soup.select(self._sel["detail_info_p"])
        for p in info_p_list:
            header = p.select_one("span.header")
            if not header:
                continue
            header_text = header.get_text(strip=True)
            content = ""
            for child in p.children:
                if getattr(child, "name", None) == "span" and "header" in child.get("class", []):
                    continue
                if hasattr(child, "get_text"):
                    content += child.get_text(strip=True)
                else:
                    content += str(child).strip()
            content = content.strip()

            if "日期" in header_text or "發售日期" in header_text:
                try:
                    movie.release_date = date.fromisoformat(content)
                except ValueError:
                    pass
            elif "長度" in header_text or "時間" in header_text:
                m = re.search(r"(\d+)", content)
                if m:
                    movie.length = int(m.group(1))
            elif "製作商" in header_text:
                movie.studio = content
            elif "發行商" in header_text:
                movie.label = content
            elif "系列" in header_text:
                movie.series = content
            elif "導演" in header_text:
                movie.director = content

        actress_boxes = soup.select(self._sel["detail_actress_box"])
        for box in actress_boxes:
            img = box.select_one(self._sel["detail_actress_img"])
            name_span = box.select_one(self._sel["detail_actress_name"])
            name = name_span.get_text(strip=True) if name_span else ""
            if not name and img:
                name = img.get("title", "")
            avatar = img.get("src", "") if img else ""
            if name:
                movie.actresses.append(ActressData(name=name, avatar_url=avatar, source="javbus"))

        genre_links = soup.select(self._sel["detail_genre"])
        for link in genre_links:
            gname = link.get_text(strip=True)
            if gname and gname not in movie.genres:
                movie.genres.append(gname)

        sample_boxes = soup.select(self._sel["detail_sample"])
        for box in sample_boxes:
            img = box.select_one("img")
            if img:
                src = img.get("src", "")
                if src:
                    movie.screenshots.append(src)

        magnet_rows = soup.select(self._sel["detail_magnet_row"])
        for row in magnet_rows:
            tds = row.find_all("td")
            if len(tds) < 2:
                continue
            link_tag = tds[0].select_one("a")
            if not link_tag:
                continue
            magnet_link = link_tag.get("href", "")
            if not magnet_link.startswith("magnet:"):
                continue
            size_text = ""
            date_text = ""
            if len(tds) >= 2:
                size_text = tds[1].get_text(strip=True)
            if len(tds) >= 3:
                date_text = tds[2].get_text(strip=True)
            size_bytes = self._parse_size(size_text)
            movie.magnets.append(MagnetData(
                link=magnet_link,
                size=size_text,
                size_bytes=size_bytes,
                date=date_text,
            ))

        return movie

    async def crawl_new_releases(self, page: int = 1) -> List[MovieData]:
        return []

    @staticmethod
    def _parse_size(size_str: str) -> Optional[int]:
        if not size_str:
            return None
        size_str = size_str.upper().replace(",", "").strip()
        m = re.match(r"([\d.]+)\s*(GB|MB|KB|G|M|K)", size_str)
        if not m:
            return None
        val = float(m.group(1))
        unit = m.group(2)
        multipliers = {"GB": 1024**3, "G": 1024**3, "MB": 1024**2, "M": 1024**2, "KB": 1024, "K": 1024}
        return int(val * multipliers.get(unit, 1))
