import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Column, Integer, String, Text, Date, DateTime, Boolean, ForeignKey,
    UniqueConstraint, Index, BigInteger,
)
from sqlalchemy.orm import relationship

from jav_meta.database.engine import Base


def _utc_now() -> datetime.datetime:
    """Return a naive UTC datetime (compatible replacement for deprecated utcnow)."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class MovieStatus(str, PyEnum):
    ACTIVE = "active"
    MISSING = "missing"
    DEPRECATED = "deprecated"


class CrawlStatus(str, PyEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ScreenshotType(str, PyEnum):
    COVER = "cover"
    POSTER = "poster"
    SCREENSHOT = "screenshot"
    ACTRESS_AVATAR = "actress_avatar"


class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(64), unique=True, nullable=False)
    enabled = Column(Boolean, default=True)
    priority = Column(Integer, default=100)
    config = Column(Text, default="{}")  # JSON string
    last_crawl_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class Movie(Base):
    __tablename__ = "movies"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(64), unique=True, nullable=False, index=True)
    title = Column(String(512), nullable=True)
    title_en = Column(String(512), nullable=True)
    title_jp = Column(String(512), nullable=True)
    release_date = Column(Date, nullable=True)
    length = Column(Integer, nullable=True)  # minutes
    studio = Column(String(128), nullable=True)
    label = Column(String(128), nullable=True)
    series = Column(String(256), nullable=True)
    director = Column(String(128), nullable=True)
    cover_url = Column(Text, nullable=True)
    cover_local = Column(Text, nullable=True)
    poster_local = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    source = Column(String(64), default="javbus")
    status = Column(String(16), default=MovieStatus.ACTIVE)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)

    actresses = relationship("Actress", secondary="movie_actress", back_populates="movies")
    genres = relationship("Genre", secondary="movie_genre", back_populates="movies")
    screenshots = relationship("Screenshot", back_populates="movie", cascade="all, delete-orphan")
    magnets = relationship("Magnet", back_populates="movie", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_movie_release_date", "release_date"),
        Index("idx_movie_studio", "studio"),
    )


class Actress(Base):
    __tablename__ = "actresses"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False, index=True)
    name_jp = Column(String(128), nullable=True)
    avatar_url = Column(Text, nullable=True)
    avatar_local = Column(Text, nullable=True)
    birthdate = Column(Date, nullable=True)
    height = Column(Integer, nullable=True)  # cm
    bust = Column(String(16), nullable=True)
    waist = Column(String(16), nullable=True)
    hip = Column(String(16), nullable=True)
    cup = Column(String(8), nullable=True)
    source = Column(String(64), default="javbus")
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)

    movies = relationship("Movie", secondary="movie_actress", back_populates="actresses")

    __table_args__ = (
        UniqueConstraint("name", "source", name="uix_actress_name_source"),
    )


class MovieActress(Base):
    __tablename__ = "movie_actress"

    movie_id = Column(Integer, ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True)
    actress_id = Column(Integer, ForeignKey("actresses.id", ondelete="CASCADE"), primary_key=True)
    role = Column(String(64), nullable=True)
    sort_order = Column(Integer, default=0)


class Genre(Base):
    __tablename__ = "genres"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(64), nullable=False, unique=True)
    source_name = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=_utc_now)

    movies = relationship("Movie", secondary="movie_genre", back_populates="genres")


class MovieGenre(Base):
    __tablename__ = "movie_genre"

    movie_id = Column(Integer, ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True)
    genre_id = Column(Integer, ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True)


class Screenshot(Base):
    __tablename__ = "screenshots"

    id = Column(Integer, primary_key=True, index=True)
    movie_id = Column(Integer, ForeignKey("movies.id", ondelete="CASCADE"), nullable=False)
    type = Column(String(32), default=ScreenshotType.SCREENSHOT)
    url = Column(Text, nullable=True)
    local_path = Column(Text, nullable=True)
    file_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=_utc_now)

    movie = relationship("Movie", back_populates="screenshots")

    __table_args__ = (
        Index("idx_screenshot_movie_type", "movie_id", "type"),
    )


class Magnet(Base):
    __tablename__ = "magnets"

    id = Column(Integer, primary_key=True, index=True)
    movie_id = Column(Integer, ForeignKey("movies.id", ondelete="CASCADE"), nullable=False)
    link = Column(Text, nullable=False)
    size = Column(String(32), nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    date = Column(String(32), nullable=True)
    source = Column(String(64), default="javbus")
    created_at = Column(DateTime, default=_utc_now)

    movie = relationship("Movie", back_populates="magnets")

    __table_args__ = (
        UniqueConstraint("movie_id", "link", name="uix_magnet_movie_link"),
    )


class CrawlLog(Base):
    __tablename__ = "crawl_logs"

    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=True)
    crawl_type = Column(String(16), nullable=False)  # full / incremental
    started_at = Column(DateTime, default=_utc_now)
    finished_at = Column(DateTime, nullable=True)
    page_from = Column(Integer, nullable=True)
    page_to = Column(Integer, nullable=True)
    items_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    fail_count = Column(Integer, default=0)
    status = Column(String(16), default=CrawlStatus.RUNNING)
    checkpoint_page = Column(Integer, nullable=True)
    checkpoint_last_date = Column(Date, nullable=True)
    message = Column(Text, nullable=True)
