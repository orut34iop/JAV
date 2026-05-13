from pathlib import Path
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class FilenameRule(BaseSettings):
    pattern: str = ""
    tag: str = ""
    description: str = ""


class JavMetaSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Paths
    project_root: Path = Field(default=Path(__file__).resolve().parent.parent)
    data_dir: Path = Field(default=Path("data"))
    db_path: Path = Field(default=Path("data/db.sqlite3"))
    dumps_dir: Path = Field(default=Path("data/dumps"))
    images_dir: Path = Field(default=Path("data/images"))
    covers_dir: Path = Field(default=Path("data/images/covers"))
    posters_dir: Path = Field(default=Path("data/images/posters"))
    screenshots_dir: Path = Field(default=Path("data/images/screenshots"))
    actresses_dir: Path = Field(default=Path("data/images/actresses"))

    # JavBus
    javbus_base_url: str = Field(default="https://www.javbus.com")
    javbus_cookie: str = Field(default="")
    javbus_uncensored_url: str = Field(default="https://www.javbus.com/uncensored")

    # Crawler
    concurrency: int = Field(default=8)
    adaptive_concurrency: bool = Field(default=True)
    request_timeout: float = Field(default=30.0)
    max_retries: int = Field(default=3)
    retry_delays: List[float] = Field(default=[1.0, 3.0, 10.0])
    request_delay: float = Field(default=0.5)
    progress_interval_seconds: int = Field(default=180)
    dump_interval_pages: int = Field(default=50)

    # Proxy (reserved)
    proxy_url: Optional[str] = Field(default=None)

    # Filename rules
    filename_rules: List[FilenameRule] = Field(default_factory=lambda: [
        FilenameRule(pattern=r"[-_][Cc]\b|中字|㊥|中文字幕", tag="中文字幕", description="Chinese subtitle"),
        FilenameRule(pattern=r"流出|leaked", tag="流出", description="Leak"),
        FilenameRule(pattern=r"\b4K\b|\bUHD\b", tag="4K", description="4K quality"),
        FilenameRule(pattern=r"\bVR\b", tag="VR", description="VR video"),
    ])

    # Output structure
    output_pattern: str = Field(default="{code} {title}")
    nfo_filename: str = Field(default="{code}.nfo")
    fanart_filename: str = Field(default="{code}-fanart.jpg")
    poster_filename: str = Field(default="{code}-poster.jpg")
    extrafanart_dir: str = Field(default="extrafanart")
    actors_dir: str = Field(default=".actors")

    def model_post_init(self, __context):
        # Resolve relative paths against project_root
        for attr in ["data_dir", "db_path", "dumps_dir", "images_dir",
                     "covers_dir", "posters_dir", "screenshots_dir", "actresses_dir"]:
            p = getattr(self, attr)
            if not p.is_absolute():
                setattr(self, attr, self.project_root / p)
            # db_path is a file, create its parent directory only
            if attr == "db_path":
                p.parent.mkdir(parents=True, exist_ok=True)
            else:
                p.mkdir(parents=True, exist_ok=True)


# Global settings instance
settings = JavMetaSettings()
