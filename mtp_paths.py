"""Path rules for Sony Walkman over MTP (libmtp / GVFS)."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

# libmtp / Walkman: keep path segments short and boring.
MAX_SEGMENT_LEN = 72

INVALID_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_mtp_segment(segment: str) -> str:
    segment = unicodedata.normalize("NFC", segment)
    segment = segment.replace("&", "and")
    for ch in ("'", "'", "`", "’", "‘", '"'):
        segment = segment.replace(ch, "")
    segment = segment.replace("[", "(").replace("]", ")")
    segment = INVALID_FS.sub("_", segment)
    segment = re.sub(r"\s+", " ", segment).strip().rstrip(".")
    ext = ""
    if segment.lower().endswith(".mp3"):
        ext = segment[-4:]
        stem = segment[:-4]
    else:
        stem = segment
    if len(segment) > MAX_SEGMENT_LEN:
        if ext:
            stem_budget = MAX_SEGMENT_LEN - len(ext) - 3
            if stem_budget < 1:
                segment = ext
            else:
                segment = stem[:stem_budget].rstrip() + "..." + ext
        else:
            segment = stem[: MAX_SEGMENT_LEN - 3].rstrip() + "..."
    return segment or "_"


def device_relative(rel: Path) -> Path:
    return Path(*[safe_mtp_segment(part) for part in rel.parts])
