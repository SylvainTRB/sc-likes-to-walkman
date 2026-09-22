"""MTP connection helpers for Sony Walkman via GVFS."""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

DEFAULT_REmount_SLEEP = 3
MUSIC_SUBPATHS = (
    "Storage Media/MUSIC",
    "Internal shared storage/MUSIC",
    "MUSIC",
)


def _gio(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gio", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def discover_mtp_uri() -> str | None:
    proc = _gio(["mount", "-l"], timeout=30)
    for line in proc.stdout.splitlines():
        if "WALKMAN" in line.upper() and "mtp://" in line:
            m = re.search(r"(mtp://[^/\s]+/)", line)
            if m:
                return m.group(1)
    proc = _gio(["mount", "-l"], timeout=30)
    for line in proc.stdout.splitlines():
        m = re.search(r"(mtp://Sony_WALKMAN[^/\s]*/)", line, re.I)
        if m:
            return m.group(1)
    return None


def gvfs_music_root(mtp_uri: str) -> Path | None:
    uid = os.getuid()
    host = mtp_uri.replace("mtp://", "").rstrip("/")
    base = Path(f"/run/user/{uid}/gvfs/mtp:host={host}")
    for sub in MUSIC_SUBPATHS:
        candidate = base / sub
        if _gio(["info", str(candidate)], timeout=12).returncode == 0:
            return candidate
    return None


def remount(mtp_uri: str) -> None:
    _gio(["mount", "-u", mtp_uri], timeout=90)
    time.sleep(DEFAULT_REmount_SLEEP)
    proc = _gio(["mount", mtp_uri], timeout=90)
    if proc.returncode != 0 and "already mounted" not in (proc.stderr or "").lower():
        raise RuntimeError(proc.stderr or proc.stdout or "gio mount failed")
    time.sleep(2)


def ensure_music_root(mtp_uri: str | None = None, remount_first: bool = True) -> Path:
    uri = mtp_uri or discover_mtp_uri()
    if not uri:
        raise RuntimeError("Walkman MTP not visible. Plug in USB, unlock, mode Transfert de fichiers.")
    if remount_first:
        try:
            remount(uri)
        except Exception:
            pass
    root = gvfs_music_root(uri)
    if root is None:
        raise RuntimeError("MUSIC folder not reachable. Débranche/rebranche le Walkman.")
    return root
