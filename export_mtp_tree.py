#!/usr/bin/env python3
"""Build local MUSIC tree with MTP-safe path names (hardlinks)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from mtp_paths import device_relative

BASE = Path(__file__).resolve().parent
STAGING = BASE / "WALKMAN_STAGING" / "MUSIC"
EXPORT = Path(os.environ.get("MTP_EXPORT_DIR", "/tmp/walkman-mtp-export/MUSIC"))


def main() -> None:
    if EXPORT.exists():
        shutil.rmtree(EXPORT)
    EXPORT.mkdir(parents=True)
    for src in STAGING.rglob("*"):
        if not src.is_file():
            continue
        rel = device_relative(src.relative_to(STAGING))
        dest = EXPORT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    count = sum(1 for _ in EXPORT.rglob("*.mp3"))
    print(f"Exported {count} MP3s to {EXPORT}")


if __name__ == "__main__":
    main()
