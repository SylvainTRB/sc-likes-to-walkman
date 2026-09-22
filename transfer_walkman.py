#!/usr/bin/env python3
"""Tag MP3s (metadata + cover) and copy to Sony Walkman MTP storage."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

from mtp_paths import device_relative, safe_mtp_segment
from mutagen.id3 import APIC, ID3, TALB, TCON, TDRC, TIT2, TPE1, TRCK, ID3NoHeaderError
from mutagen.mp3 import MP3

CLIENT_ID = os.environ.get("SC_CLIENT_ID", "Pb72ranhoyt6gw7hM7TkzUItXlMWSNSo")
OAUTH = os.environ.get("SC_OAUTH_TOKEN", "").strip()
USER_ID = os.environ.get("SC_USER_ID", "").strip()

BASE = Path(__file__).resolve().parent
MUSIC_SRC = Path(os.environ.get("SC_OUTPUT_DIR", BASE / "MUSIC"))
STAGING = Path(os.environ.get("WALKMAN_STAGING", BASE / "WALKMAN_STAGING" / "MUSIC"))
MANIFEST = BASE / "manifest.json"
CACHE = BASE / "tracks_cache.json"
SYNC_STATE = BASE / "walkman_sync_state.json"
INVALID_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize(name: str, max_len: int = 120) -> str:
    name = unicodedata.normalize("NFC", name)
    name = INVALID_FS.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(".")
    if len(name) > max_len:
        name = name[: max_len - 3].rstrip() + "..."
    return name or "Unknown"


def mtp_filename(name: str, track_id: int, max_len: int = 100) -> str:
    """ASCII-only names for flaky MTP stacks; full Unicode stays in ID3 tags."""
    plain = unicodedata.normalize("NFKD", name)
    plain = plain.encode("ascii", "ignore").decode()
    plain = sanitize(plain, max_len=max_len)
    if not plain or plain == "Unknown":
        plain = f"track_{track_id}" if track_id else "track"
    return f"{plain}.mp3"


def mtp_path_ok(path: Path) -> bool:
    try:
        proc = subprocess.run(
            ["timeout", "8", "gio", "info", str(path)],
            capture_output=True,
            text=True,
        )
        return proc.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def find_walkman_music_root() -> Path:
    if path := os.environ.get("WALKMAN_MUSIC_ROOT"):
        return Path(path)
    uid = os.getuid()
    gvfs = Path(f"/run/user/{uid}/gvfs")
    if gvfs.is_dir():
        for mtp in gvfs.glob("mtp:host=*"):
            for candidate in (
                mtp / "Storage Media" / "MUSIC",
                mtp / "Internal shared storage" / "MUSIC",
                mtp / "MUSIC",
            ):
                if mtp_path_ok(candidate):
                    return candidate
    raise SystemExit(
        "Walkman not found. Plug it in (MTP), or set WALKMAN_MUSIC_ROOT to .../Storage Media/MUSIC"
    )


def api_get(url: str) -> dict:
    headers = {"Accept": "application/json", "User-Agent": "sc-likes-to-walkman/1.0"}
    if OAUTH:
        headers["Authorization"] = OAUTH
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def fetch_track(track_id: int, cache: dict) -> dict:
    key = str(track_id)
    if key in cache:
        return cache[key]
    url = f"https://api-v2.soundcloud.com/tracks/{track_id}?client_id={urllib.parse.quote(CLIENT_ID)}"
    track = api_get(url)
    cache[key] = track
    return track


def mtp_ensure_parent(dest: Path, dest_root: Path) -> None:
    rel_parent = dest.parent.relative_to(dest_root)
    cur = dest_root
    for part in rel_parent.parts:
        cur = cur / part
        subprocess.run(
            ["gio", "mkdir", str(cur)],
            check=False,
            timeout=45,
            capture_output=True,
            text=True,
        )


def mtp_copy(src: Path, dest: Path, dest_root: Path | None = None) -> None:
    if dest_root is not None:
        mtp_ensure_parent(dest, dest_root)
    last_err: subprocess.CalledProcessError | None = None
    for attempt in range(4):
        try:
            subprocess.run(
                ["gio", "copy", "-p", str(src.resolve()), str(dest)],
                check=True,
                timeout=600,
                capture_output=True,
                text=True,
            )
            return
        except subprocess.CalledProcessError as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    if last_err:
        err = (last_err.stderr or last_err.stdout or "").strip()
        raise RuntimeError(err or str(last_err))


def download_bytes(url: str) -> bytes:
    if not url:
        return b""
    if url.startswith("//"):
        url = "https:" + url
    req = urllib.request.Request(url, headers={"User-Agent": "sc-likes-to-walkman/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def artwork_url(track: dict) -> str | None:
    url = track.get("artwork_url")
    if not url:
        user = track.get("user") or {}
        url = user.get("avatar_url")
    if not url:
        return None
    return url.replace("-large", "-t500x500")


def album_name(track: dict) -> str:
    meta = track.get("publisher_metadata") or {}
    for key in ("album_title", "release_title"):
        if meta.get(key):
            return str(meta[key])
    if track.get("label_name"):
        return str(track["label_name"])
    rd = track.get("release_date") or track.get("display_date") or track.get("created_at")
    year = rd[:4] if rd and len(rd) >= 4 else ""
    return f"SoundCloud Likes {year}".strip()


def artist_name(track: dict) -> str:
    meta = track.get("publisher_metadata") or {}
    if meta.get("artist"):
        return str(meta["artist"])
    user = track.get("user") or {}
    return user.get("username") or user.get("full_name") or "Unknown Artist"


def year_from_track(track: dict) -> str:
    for key in ("release_date", "display_date", "created_at"):
        val = track.get(key)
        if val and len(val) >= 4:
            return val[:4]
    return ""


def apply_tags(mp3_path: Path, track: dict, cover: bytes, mime: str) -> None:
    try:
        tags = ID3(mp3_path)
    except ID3NoHeaderError:
        tags = ID3()
    title = track.get("title") or mp3_path.stem
    artist = artist_name(track)
    album = album_name(track)
    genre = track.get("genre") or ""
    year = year_from_track(track)

    tags["TIT2"] = TIT2(encoding=3, text=title)
    tags["TPE1"] = TPE1(encoding=3, text=artist)
    tags["TALB"] = TALB(encoding=3, text=album)
    if genre:
        tags["TCON"] = TCON(encoding=3, text=genre)
    if year:
        tags["TDRC"] = TDRC(encoding=3, text=year)
    if cover:
        tags.delall("APIC")
        tags.add(
            APIC(
                encoding=3,
                mime=mime,
                type=3,
                desc="Cover",
                data=cover,
            )
        )
    tags.save(mp3_path)


def cover_mime_and_jpeg(data: bytes) -> tuple[bytes, str]:
    if data[:3] == b"\xff\xd8\xff":
        return data, "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        try:
            from io import BytesIO

            from PIL import Image

            img = Image.open(BytesIO(data)).convert("RGB")
            out = BytesIO()
            img.save(out, format="JPEG", quality=90)
            return out.getvalue(), "image/jpeg"
        except ImportError:
            return data, "image/png"
    return data, "image/jpeg"


def load_entries() -> list[tuple[int, Path]]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries: list[tuple[int, Path]] = []
    for row in manifest.get("downloads", []):
        status = row.get("status")
        if status not in ("ok", "exists") and not (
            isinstance(status, str) and status.startswith("ok")
        ):
            continue
        path = row.get("file")
        tid = row.get("id")
        if tid and path and Path(path).is_file():
            entries.append((int(tid), Path(path)))
    if not entries:
        for p in sorted(MUSIC_SRC.glob("*.mp3")):
            entries.append((0, p))
    return entries


def stage_track(
    track_id: int,
    src: Path,
    track: dict,
    cover: bytes,
    mime: str,
) -> Path:
    artist = sanitize(
        unicodedata.normalize("NFKD", artist_name(track)).encode("ascii", "ignore").decode()
        or artist_name(track)
    )
    album = sanitize(
        unicodedata.normalize("NFKD", album_name(track)).encode("ascii", "ignore").decode()
        or album_name(track)
    )
    title = track.get("title") or src.stem
    dest_dir = STAGING / artist / album
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file = dest_dir / mtp_filename(title, track_id)
    if not dest_file.exists() or dest_file.stat().st_size != src.stat().st_size:
        shutil.copy2(src, dest_file)
    apply_tags(dest_file, track, cover, mime)
    if cover:
        (dest_dir / "folder.jpg").write_bytes(cover)
    return dest_file


def sync_staging_to_walkman(dest_root: Path, staged_files: list[Path]) -> int:
    synced = 0
    state: dict = {"synced": []}
    if SYNC_STATE.exists():
        state = json.loads(SYNC_STATE.read_text(encoding="utf-8"))
    done = set(state.get("synced", []))

    failed: list[str] = []
    for staged in staged_files:
        rel = staged.relative_to(STAGING)
        key = str(rel)
        if key in done:
            synced += 1
            continue
        dest = dest_root / device_relative(rel)
        print(f"sync → {dest.relative_to(dest_root)}")
        try:
            mtp_copy(staged, dest, dest_root)
        except Exception as e:
            print(f"  FAILED: {e}")
            failed.append(key)
            continue
        done.add(key)
        state["synced"] = sorted(done)
        SYNC_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        synced += 1
        time.sleep(0.35)
    if failed:
        print(f"Failed ({len(failed)}): see log above")
    return synced


def main() -> int:
    if os.environ.get("WALKMAN_SYNC_ONLY") == "1":
        staged_files = sorted(STAGING.rglob("*.mp3"))
        if not staged_files:
            print(f"No staged MP3s in {STAGING}. Run without WALKMAN_SYNC_ONLY first.")
            return 1
        print(f"Sync-only: {len(staged_files)} staged tracks")
        try:
            dest_root = find_walkman_music_root()
        except (SystemExit, OSError) as e:
            print(e)
            print("Unplug/replug the Walkman (MTP), unlock screen, then retry.")
            return 1
        synced = sync_staging_to_walkman(dest_root, staged_files)
        print(f"Synced to Walkman: {synced}/{len(staged_files)}")
        return 0

    cache: dict = {}
    if CACHE.exists():
        cache = json.loads(CACHE.read_text(encoding="utf-8"))

    entries = load_entries()
    print(f"Tracks to process: {len(entries)}")

    staged_files: list[Path] = []
    for i, (track_id, src) in enumerate(entries, 1):
        if track_id:
            try:
                track = fetch_track(track_id, cache)
            except Exception as e:
                print(f"[{i}] API skip {src.name}: {e}")
                continue
        else:
            track = {"title": src.stem, "user": {"username": "Unknown"}}

        title = track.get("title") or src.stem
        art_url = artwork_url(track)
        cover = b""
        mime = "image/jpeg"
        if art_url:
            try:
                raw = download_bytes(art_url)
                cover, mime = cover_mime_and_jpeg(raw)
            except Exception:
                pass

        print(f"[{i}] stage {artist_name(track)} / {album_name(track)} / {title}")
        try:
            staged_files.append(stage_track(track_id, src, track, cover, mime))
        except Exception as e:
            print(f"  stage failed: {e}")

    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print(f"Staged {len(staged_files)} tracks under {STAGING}")

    try:
        dest_root = find_walkman_music_root()
    except SystemExit as e:
        print(e)
        print("Unplug/replug the Walkman, then re-run to sync staging → device.")
        return 0
    except OSError as e:
        print(f"Walkman MTP not readable ({e}). Staging is ready at {STAGING}")
        print("Unplug/replug USB, unlock the player, then re-run this script to sync.")
        return 0

    print(f"Walkman MUSIC: {dest_root}")
    synced = sync_staging_to_walkman(dest_root, staged_files)
    print(f"Synced to Walkman: {synced}/{len(staged_files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
