#!/usr/bin/env python3
"""Remove Walkman files not present in WALKMAN_STAGING/MUSIC."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
STAGING = Path(os.environ.get("WALKMAN_STAGING", BASE / "WALKMAN_STAGING" / "MUSIC"))

from mtp_paths import device_relative


def find_walkman_music_root() -> Path:
    if path := os.environ.get("WALKMAN_MUSIC_ROOT"):
        return Path(path)
    uid = os.getuid()
    for mtp in Path(f"/run/user/{uid}/gvfs").glob("mtp:host=*"):
        for candidate in (
            mtp / "Storage Media" / "MUSIC",
            mtp / "MUSIC",
        ):
            if candidate.is_dir():
                return candidate
    raise SystemExit("Walkman MUSIC folder not found (MTP mounted?)")


def gio_remove(target: Path) -> None:
    subprocess.run(["gio", "remove", str(target)], check=True, timeout=120)


def allowed_paths(staging: Path) -> set[str]:
    return {
        device_relative(f.relative_to(staging)).as_posix()
        for f in staging.rglob("*")
        if f.is_file()
    }


def main() -> int:
    dry = os.environ.get("DRY_RUN") == "1"
    staging = STAGING
    if not staging.is_dir():
        print(f"Missing staging: {staging}", file=sys.stderr)
        return 1

    device = find_walkman_music_root()
    allowed = allowed_paths(staging)
    print(f"Keep {len(allowed)} staged paths")
    print(f"Device: {device}")

    to_delete: list[Path] = []
    for root, _dirs, files in os.walk(device):
        root_path = Path(root)
        for name in files:
            full = root_path / name
            rel = full.relative_to(device).as_posix()
            if rel not in allowed:
                to_delete.append(full)

    print(f"Delete {len(to_delete)} files")
    if dry:
        for p in to_delete[:20]:
            print("  would delete", p.relative_to(device))
        if len(to_delete) > 20:
            print(f"  ... and {len(to_delete) - 20} more")
        return 0

    deleted = 0
    for i, path in enumerate(to_delete, 1):
        try:
            print(f"[{i}/{len(to_delete)}] rm {path.relative_to(device)}")
            gio_remove(path)
            deleted += 1
        except subprocess.CalledProcessError as e:
            print(f"  failed: {e}")
    # Drop empty album/artist folders (not part of staged tree)
    staging_dir_prefixes: set[str] = set()
    for rel in allowed:
        parts = rel.split("/")
        for i in range(len(parts) - 1):
            staging_dir_prefixes.add("/".join(parts[: i + 1]))

    removed_dirs = 0
    for root, dirs, files in os.walk(device, topdown=False):
        root_path = Path(root)
        if root_path == device:
            continue
        rel = root_path.relative_to(device).as_posix()
        if rel in staging_dir_prefixes:
            continue
        try:
            if not any(root_path.iterdir()):
                gio_remove(root_path)
                removed_dirs += 1
        except (OSError, subprocess.CalledProcessError):
            pass

    print(f"Done. Removed {deleted} files and {removed_dirs} empty folders.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
