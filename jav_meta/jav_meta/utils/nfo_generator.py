import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from jav_meta.database.models import Movie


def generate_nfo(movie: Movie, tags: List[str] = None) -> str:
    root = ET.Element("movie")

    _add_child(root, "title", movie.title or movie.code)
    _add_child(root, "originaltitle", movie.title_jp or movie.title or "")
    _add_child(root, "sorttitle", movie.code)
    _add_child(root, "set", movie.series or "")
    _add_child(root, "studio", movie.studio or "")
    _add_child(root, "premiered", str(movie.release_date) if movie.release_date else "")
    _add_child(root, "year", str(movie.release_date.year) if movie.release_date else "")
    _add_child(root, "runtime", str(movie.length) if movie.length else "")
    _add_child(root, "mpaa", "NC-17")
    _add_child(root, "plot", movie.description or "")
    _add_child(root, "outline", movie.description or "")

    # Genres
    if movie.genres:
        for g in movie.genres:
            _add_child(root, "genre", g)
            _add_child(root, "tag", g)

    # Extra tags from filename rules
    if tags:
        for t in tags:
            _add_child(root, "tag", t)
            if t not in [g.name for g in movie.genres]:
                _add_child(root, "genre", t)

    # Actors
    if movie.actresses:
        for a in movie.actresses:
            actor_el = ET.SubElement(root, "actor")
            _add_child(actor_el, "name", a.name)
            _add_child(actor_el, "thumb", a.avatar_local or a.avatar_url or "")
            _add_child(actor_el, "role", "")

    # Uniqueid
    uid = ET.SubElement(root, "uniqueid")
    uid.set("type", "javbus")
    uid.set("default", "true")
    uid.text = movie.code

    # Cover art hints
    if movie.cover_local:
        fanart = ET.SubElement(root, "fanart")
        thumb = ET.SubElement(fanart, "thumb")
        thumb.text = movie.cover_local
    if movie.poster_local:
        thumb = ET.SubElement(root, "thumb")
        thumb.set("aspect", "poster")
        thumb.text = movie.poster_local

    # Indent for readability
    _indent(root)
    return ET.tostring(root, encoding="unicode")


def save_nfo(movie: Movie, dest_dir: Path, filename: Optional[str] = None, tags: List[str] = None):
    if filename is None:
        filename = f"{movie.code}.nfo"
    dest_dir.mkdir(parents=True, exist_ok=True)
    nfo_path = dest_dir / filename
    nfo_content = generate_nfo(movie, tags=tags)
    nfo_path.write_text(nfo_content, encoding="utf-8")
    return nfo_path


def _add_child(parent, tag, text):
    el = ET.SubElement(parent, tag)
    el.text = text
    return el


def _indent(elem, level=0):
    i = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
        for child in elem:
            _indent(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i
