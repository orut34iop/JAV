from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional


@dataclass
class MagnetData:
    link: str
    size: Optional[str] = None
    size_bytes: Optional[int] = None
    date: Optional[str] = None


@dataclass
class ActressData:
    name: str
    name_jp: Optional[str] = None
    avatar_url: Optional[str] = None
    source: str = "javbus"


@dataclass
class MovieData:
    code: str
    title: Optional[str] = None
    title_en: Optional[str] = None
    title_jp: Optional[str] = None
    release_date: Optional[date] = None
    length: Optional[int] = None
    studio: Optional[str] = None
    label: Optional[str] = None
    series: Optional[str] = None
    director: Optional[str] = None
    cover_url: Optional[str] = None
    poster_url: Optional[str] = None
    description: Optional[str] = None
    source: str = "javbus"
    actresses: List[ActressData] = field(default_factory=list)
    genres: List[str] = field(default_factory=list)
    screenshots: List[str] = field(default_factory=list)
    magnets: List[MagnetData] = field(default_factory=list)


class BaseScraper(ABC):
    name: str = ""
    priority: int = 100

    @abstractmethod
    async def crawl_list_page(self, page: int, uncensored: bool = False) -> List[tuple]:
        """Return list of (code, detail_url, thumb_url)"""
        ...

    @abstractmethod
    async def get_movie_detail(self, code: str, detail_url: str) -> Optional[MovieData]:
        ...

    @abstractmethod
    async def crawl_new_releases(self, page: int = 1) -> List[MovieData]:
        ...
