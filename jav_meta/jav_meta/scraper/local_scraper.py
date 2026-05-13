import shutil
from collections import defaultdict
from pathlib import Path
from typing import List, Optional

from loguru import logger
from sqlalchemy.orm import Session

from jav_meta.config import settings
from jav_meta.database.engine import SessionLocal
from jav_meta.database.models import Actress, Movie, Screenshot, ScreenshotType
from jav_meta.utils.code_matcher import extract_code, is_video_file, detect_tags
from jav_meta.utils.nfo_generator import save_nfo


class LocalScraper:
    def __init__(self, db: Session = None):
        self.db = db or SessionLocal()

    def close(self):
        if self.db:
            self.db.close()

    def scrape_folder(
        self,
        folder: Path,
        write_nfo: bool = True,
        download_cover: bool = True,
        download_screenshots: bool = True,
        copy_actor_photos: bool = True,
        rename_file: bool = False,
        create_folder: bool = False,
        move_to_folder: bool = False,
        dry_run: bool = False,
    ):
        folder = Path(folder).resolve()
        if not folder.exists():
            logger.error(f"Folder not found: {folder}")
            return

        # Collect video files grouped by code within the same directory
        video_files = [f for f in folder.iterdir() if f.is_file() and is_video_file(f.name)]
        grouped = defaultdict(list)
        for vf in video_files:
            code = extract_code(vf.name)
            if code:
                grouped[code].append(vf)
            else:
                logger.warning(f"Could not extract code from: {vf.name}")

        for code, files in grouped.items():
            self._process_group(
                code=code,
                files=files,
                folder=folder,
                write_nfo=write_nfo,
                download_cover=download_cover,
                download_screenshots=download_screenshots,
                copy_actor_photos=copy_actor_photos,
                rename_file=rename_file,
                create_folder=create_folder,
                move_to_folder=move_to_folder,
                dry_run=dry_run,
            )

    def _process_group(
        self,
        code: str,
        files: List[Path],
        folder: Path,
        write_nfo: bool,
        download_cover: bool,
        download_screenshots: bool,
        copy_actor_photos: bool,
        rename_file: bool,
        create_folder: bool,
        move_to_folder: bool,
        dry_run: bool,
    ):
        movie = self.db.query(Movie).filter(Movie.code == code).first()
        if not movie:
            logger.warning(f"[{code}] Not found in local database, skipping.")
            return

        tags = detect_tags(files[0].name)
        logger.info(f"[{code}] Found in DB: {movie.title or '(no title)'}. Files: {len(files)}")

        # Determine target folder
        if create_folder or move_to_folder:
            target_folder = folder / code
            if not dry_run:
                target_folder.mkdir(parents=True, exist_ok=True)
        else:
            target_folder = folder

        # NFO
        if write_nfo:
            nfo_name = settings.nfo_filename.format(code=code)
            if not dry_run:
                save_nfo(movie, target_folder, filename=nfo_name, tags=tags)
            logger.info(f"[{code}] NFO written: {nfo_name}")

        # Cover / Poster
        if download_cover:
            if movie.cover_local:
                src = settings.project_root / movie.cover_local
                if src.exists():
                    fanart_name = settings.fanart_filename.format(code=code)
                    dest = target_folder / fanart_name
                    if not dry_run:
                        shutil.copy2(src, dest)
                    logger.info(f"[{code}] Fanart copied: {fanart_name}")
            if movie.poster_local:
                src = settings.project_root / movie.poster_local
                if src.exists():
                    poster_name = settings.poster_filename.format(code=code)
                    dest = target_folder / poster_name
                    if not dry_run:
                        shutil.copy2(src, dest)
                    logger.info(f"[{code}] Poster copied: {poster_name}")

        # Screenshots -> extrafanart
        if download_screenshots:
            screenshots = self.db.query(Screenshot).filter(
                Screenshot.movie_id == movie.id,
                Screenshot.type == ScreenshotType.SCREENSHOT,
            ).all()
            if screenshots:
                extra_dir = target_folder / settings.extrafanart_dir
                if not dry_run:
                    extra_dir.mkdir(parents=True, exist_ok=True)
                for idx, ss in enumerate(screenshots):
                    src = settings.project_root / ss.local_path if ss.local_path else None
                    if src and src.exists():
                        ext = src.suffix
                        dest = extra_dir / f"{idx + 1:02d}{ext}"
                        if not dry_run:
                            shutil.copy2(src, dest)
                logger.info(f"[{code}] Screenshots copied: {len(screenshots)}")

        # Actor photos -> .actors
        if copy_actor_photos and movie.actresses:
            actors_dir = target_folder / settings.actors_dir
            if not dry_run:
                actors_dir.mkdir(parents=True, exist_ok=True)
            for actress in movie.actresses:
                if actress.avatar_local:
                    src = settings.project_root / actress.avatar_local
                    if src.exists():
                        safe_name = "".join(c for c in actress.name if c.isalnum() or c in "_-")
                        dest = actors_dir / f"{safe_name}{src.suffix}"
                        if not dry_run:
                            shutil.copy2(src, dest)
            logger.info(f"[{code}] Actor photos copied: {len(movie.actresses)}")

        # Move / rename files
        for vf in files:
            if rename_file and not move_to_folder:
                import re as _re
                new_name = f"{code}{vf.suffix}"
                if len(files) > 1:
                    # Handle multi-part by preserving part marker
                    part_match = _re.search(r'(cd|part|pt)[-_]?([0-9A-Da-d])', vf.stem, _re.IGNORECASE)
                    if part_match:
                        new_name = f"{code}-{part_match.group(0)}{vf.suffix}"
                dest = target_folder / new_name
                if not dry_run and vf != dest:
                    vf.rename(dest)
                logger.info(f"[{code}] Renamed: {new_name}")
            elif move_to_folder:
                dest = target_folder / vf.name
                if not dry_run and vf != dest:
                    shutil.move(str(vf), str(dest))

    def search(self, query: str) -> List[Movie]:
        return self.db.query(Movie).filter(
            Movie.code.ilike(f"%{query}%") | Movie.title.ilike(f"%{query}%")
        ).limit(20).all()

    def stats(self) -> dict:
        total_movies = self.db.query(Movie).count()
        total_actresses = self.db.query(Actress).count()
        return {
            "movies": total_movies,
            "actresses": total_actresses,
        }
