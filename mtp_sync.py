#!/usr/bin/env python3
"""
Reliable Walkman sync: export MTP-safe tree, remount on errors, verify sizes.

Usage:
  python3 mtp_sync.py              # full sync (resume)
  python3 mtp_sync.py --remount    # force remount at start
  python3 mtp_sync.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from export_mtp_tree import EXPORT, main as build_export
from mtp_connection import discover_mtp_uri, ensure_music_root, remount
from mtp_paths import device_relative

BASE = Path(__file__).resolve().parent
STATE_PATH = BASE / "mtp_sync_state.json"
REMOUNT_EVERY = int(__import__("os").environ.get("MTP_REMOUNT_EVERY", "12"))
COPY_TIMEOUT = int(__import__("os").environ.get("MTP_COPY_TIMEOUT", "240"))


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"ok": [], "failed": {}}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def gio_mkdir(path: Path) -> None:
    subprocess.run(
        ["gio", "mkdir", "-p", str(path)],
        capture_output=True,
        text=True,
        timeout=90,
    )


def file_size_gio(path: Path) -> int | None:
    proc = subprocess.run(
        ["gio", "info", "-a", "standard::size", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        if "size:" in line.lower():
            try:
                return int(line.split(":")[-1].strip())
            except ValueError:
                return None
    return None


def gio_copy(src: Path, dest_dir: Path) -> None:
    gio_mkdir(dest_dir)
    proc = subprocess.run(
        ["gio", "copy", "-p", str(src.resolve()), str(dest_dir) + "/"],
        capture_output=True,
        text=True,
        timeout=COPY_TIMEOUT,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(err or f"gio copy failed ({proc.returncode})")


def sync_file(
    src: Path,
    rel: Path,
    music_root: Path,
    mtp_uri: str,
    state: dict,
    dry_run: bool,
) -> bool:
    key = rel.as_posix()
    if key in state.get("ok", []):
        return True

    dest_rel = device_relative(rel)
    dest_dir = music_root / dest_rel.parent
    dest_file = dest_dir / dest_rel.name
    expected = src.stat().st_size

    if not dry_run:
        existing = file_size_gio(dest_file)
        if existing == expected:
            state.setdefault("ok", []).append(key)
            save_state(state)
            return True

    if dry_run:
        print(f"would copy {key} -> {dest_rel}")
        return True

    for attempt in range(3):
        try:
            gio_copy(src, dest_dir)
            got = file_size_gio(dest_file)
            if got == expected:
                state.setdefault("ok", []).append(key)
                state.get("failed", {}).pop(key, None)
                save_state(state)
                return True
            raise RuntimeError(f"size mismatch (got {got}, want {expected})")
        except Exception as e:
            if attempt < 2:
                print(f"  retry {attempt + 1}/2 after: {e}")
                try:
                    remount(mtp_uri)
                    time.sleep(2)
                    music_root = ensure_music_root(mtp_uri, remount_first=False)
                except Exception as re_err:
                    print(f"  remount failed: {re_err}")
                    print("  Débranche/rebranche le Walkman, puis relance mtp_sync.py --remount")
                    state.setdefault("failed", {})[key] = str(e)
                    save_state(state)
                    return False
            else:
                state.setdefault("failed", {})[key] = str(e)
                save_state(state)
                print(f"  FAIL {key}: {e}")
                return False
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--remount", action="store_true", help="Remount MTP before sync")
    parser.add_argument("--no-export", action="store_true", help="Skip rebuilding /tmp export")
    args = parser.parse_args()

    if not args.no_export:
        build_export()

    if not EXPORT.is_dir():
        print("Export missing; run export_mtp_tree.py", file=sys.stderr)
        return 1

    files = sorted(p for p in EXPORT.rglob("*.mp3") if p.is_file())
    print(f"MP3 to sync: {len(files)} (covers/folder.jpg skipped)")

    mtp_uri = discover_mtp_uri()
    if not mtp_uri:
        print("Walkman not detected.", file=sys.stderr)
        return 1

    music_root = ensure_music_root(mtp_uri, remount_first=args.remount)
    print(f"MUSIC: {music_root}")

    state = load_state()
    ok_before = len(state.get("ok", []))
    copied_since_remount = 0

    for i, src in enumerate(files, 1):
        rel = src.relative_to(EXPORT)
        print(f"[{i}/{len(files)}] {rel}")
        if copied_since_remount >= REMOUNT_EVERY:
            print("  (preventive remount)")
            remount(mtp_uri)
            music_root = ensure_music_root(mtp_uri, remount_first=False)
            copied_since_remount = 0
            time.sleep(1)
        if sync_file(src, rel, music_root, mtp_uri, state, args.dry_run):
            copied_since_remount += 1
        time.sleep(0.25)

    ok = len(state.get("ok", []))
    failed = len(state.get("failed", {}))
    print(f"Done. OK: {ok} (+{ok - ok_before} this run), failed: {failed}")
    if failed:
        print(f"Retry: python3 {BASE / 'mtp_sync.py'} --remount")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
