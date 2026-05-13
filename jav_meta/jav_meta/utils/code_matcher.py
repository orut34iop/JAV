import re
from pathlib import Path
from typing import Optional


# Common JAV code patterns
CODE_PATTERNS = [
    # Standard: ABC-123, ABC_123, ABC 123
    re.compile(r"([A-Za-z]{2,8})[-_ ]?(\d{2,5})"),
    # FC2 special: FC2-PPV-123456
    re.compile(r"(FC2[-_]?PPV[-_]?)(\d{6,7})"),
    # Caribbean / 1pondo / HEYZO numeric
    re.compile(r"(\d{6})[-_](\d{2,3})"),
    # Tokyo-Hot special
    re.compile(r"(Tokyo[-_]?Hot)[-_]?(\d{3,5})", re.IGNORECASE),
    # S2M, SMD etc with special numbering
    re.compile(r"([A-Za-z]\d[A-Za-z])[-_ ]?(\d{2,4})"),
]

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".wmv", ".iso", ".rmvb", ".flv", ".ts", ".m2ts", ".mov"}


def extract_code(filename: str) -> Optional[str]:
    """Extract and normalize JAV code from filename."""
    name = Path(filename).stem
    # Remove common quality/suffix tags to avoid interference
    cleaned = re.sub(r"[-_](1080p|720p|480p|FHD|HD|UHD|4K|VR|3D|HEVC|x265|x264|h264|h265)\b", "", name, flags=re.IGNORECASE)
    cleaned = re.sub(r"[-_]( leak|leaked|流出|[-_][Cc]\b|中字|㊥|中文字幕)\b?.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[-_](cd|part|pt|vol)[-_]?[0-9A-Da-d]\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip("-_ ")

    for pattern in CODE_PATTERNS:
        match = pattern.search(cleaned)
        if match:
            prefix = match.group(1).upper()
            number = match.group(2)
            # Normalize FC2
            if prefix.startswith("FC2"):
                return f"FC2-PPV-{number}"
            # Normalize Tokyo-Hot
            if "TOKYO" in prefix:
                return f"Tokyo-Hot-{number}"
            return f"{prefix}-{number}"
    return None


def is_video_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in VIDEO_EXTS


def detect_tags(filename: str) -> list:
    """Detect tags from filename based on configured rules."""
    from jav_meta.config import settings
    tags = []
    name = filename.lower()
    for rule in settings.filename_rules:
        if not rule.pattern:
            continue
        try:
            if re.search(rule.pattern, name):
                tags.append(rule.tag)
        except re.error:
            continue
    return tags
