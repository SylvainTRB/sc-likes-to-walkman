#!/usr/bin/env python3
"""Fetch SoundCloud track likes and download tracks in Walkman-friendly MP3 layout."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CLIENT_ID = os.environ.get("SC_CLIENT_ID", "Pb72ranhoyt6gw7hM7TkzUItXlMWSNSo")
OAUTH = os.environ.get("SC_OAUTH_TOKEN", "").strip()
USER_ID = os.environ.get("SC_USER_ID", "").strip()

MIN_MS = int(os.environ.get("SC_MIN_DURATION_MS", str(2 * 60 * 1000 + 1)))  # strictly above 2 min
MAX_MS = int(os.environ.get("SC_MAX_DURATION_MS", str(10 * 60 * 1000 - 1)))  # strictly below 10 min

OUT_DIR = Path(os.environ.get("SC_OUTPUT_DIR", Path(__file__).resolve().parent / "MUSIC"))
MANIFEST_PATH = OUT_DIR.parent / "manifest.json"
YTDLP = os.environ.get("YTDLP") or shutil.which("yt-dlp") or "yt-dlp"

INVALID_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def api_get(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": OAUTH,
            "Accept": "application/json",
            "User-Agent": "sc-likes-to-walkman/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)


def sanitize_filename(name: str, max_len: int = 180) -> str:
    name = INVALID_FS.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(".")
    if len(name) > max_len:
        name = name[: max_len - 3].rstrip() + "..."
    return name or "unknown"


def transcoding_query_url(track: dict, tr: dict) -> str:
    auth = track["track_authorization"]
    return (
        f"{tr['url']}?client_id={urllib.parse.quote(CLIENT_ID)}"
        f"&track_authorization={urllib.parse.quote(auth)}"
    )


def pick_transcoding(track: dict) -> tuple[dict, str] | None:
    auth = track.get("track_authorization")
    if not auth:
        return None
    transcodings = track.get("media", {}).get("transcodings", [])
    if not transcodings:
        return None

    def score(tr: dict) -> int:
        fmt = tr.get("format") or {}
        preset = tr.get("preset") or ""
        protocol = fmt.get("protocol") or ""
        points = 0
        if protocol == "progressive":
            points += 100
        if "mp3" in preset:
            points += 50
        elif preset == "aac_160k":
            points += 45
        elif preset == "aac_96k":
            points += 30
        elif protocol == "hls":
            points += 15
        return points

    best = max(transcodings, key=score)
    return best, transcoding_query_url(track, best)


def resolve_mp3_url(transcoding_url: str) -> str:
    data = api_get(transcoding_url)
    url = data.get("url")
    if not url:
        raise RuntimeError("no stream url in transcoding response")
    return url


def download_file(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "sc-likes-to-walkman/1.0"})
    with urllib.request.urlopen(req, timeout=600) as resp, open(dest, "wb") as f:
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)


def download_hls_to_mp3(hls_url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        hls_url,
        "-vn",
        "-codec:a",
        "libmp3lame",
        "-q:a",
        "2",
        str(dest),
    ]
    subprocess.run(cmd, check=True, timeout=900)


def download_ytdlp(permalink_url: str, dest: Path) -> None:
    if not Path(YTDLP).exists():
        raise RuntimeError("yt-dlp not found")
    dest.parent.mkdir(parents=True, exist_ok=True)
    out_template = str(dest.with_suffix(".%(ext)s"))
    cmd = [
        YTDLP,
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",
        "-o",
        out_template,
        permalink_url,
    ]
    subprocess.run(cmd, check=True, timeout=900)


def tag_mp3(path: Path, title: str, artist: str) -> None:
    try:
        from mutagen.id3 import ID3, TIT2, TPE1
    except ImportError:
        return
    try:
        try:
            tags = ID3(path)
        except Exception:
            tags = ID3()
        tags["TIT2"] = TIT2(encoding=3, text=title)
        tags["TPE1"] = TPE1(encoding=3, text=artist)
        tags.save(path)
    except Exception:
        pass


def fetch_all_likes() -> list[dict]:
    likes: list[dict] = []
    url = (
        f"https://api-v2.soundcloud.com/users/{USER_ID}/track_likes"
        f"?limit=200&client_id={urllib.parse.quote(CLIENT_ID)}&app_locale=en"
    )
    page = 0
    while url:
        page += 1
        print(f"Fetching likes page {page}...", flush=True)
        data = api_get(url)
        for item in data.get("collection", []):
            track = item.get("track")
            if track:
                likes.append(
                    {
                        "liked_at": item.get("created_at"),
                        "track": track,
                    }
                )
        url = data.get("next_href")
        if url and "client_id=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}client_id={urllib.parse.quote(CLIENT_ID)}"
        time.sleep(0.25)
    return likes


def main() -> int:
    if not OAUTH:
        print("Set SC_OAUTH_TOKEN (e.g. 'OAuth 2-...')", file=sys.stderr)
        return 1
    if not USER_ID:
        print("Set SC_USER_ID (numeric SoundCloud user id)", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_likes = fetch_all_likes()
    print(f"Total likes with track payload: {len(all_likes)}")

    eligible: list[dict] = []
    skipped_duration: list[dict] = []
    skipped_other: list[dict] = []

    for entry in all_likes:
        track = entry["track"]
        dur = track.get("duration") or 0
        if not track.get("streamable"):
            skipped_other.append({"id": track.get("id"), "title": track.get("title"), "reason": "not streamable"})
            continue
        if dur <= MIN_MS or dur >= MAX_MS:
            skipped_duration.append(
                {
                    "id": track.get("id"),
                    "title": track.get("title"),
                    "duration_ms": dur,
                    "duration_min": round(dur / 60000, 2),
                }
            )
            continue
        eligible.append(entry)

    print(f"Eligible ({MIN_MS/1000:.0f}s < dur < {MAX_MS/1000:.0f}s): {len(eligible)}")
    print(f"Skipped duration: {len(skipped_duration)}")

    manifest = {
        "user_id": USER_ID,
        "min_duration_ms": MIN_MS,
        "max_duration_ms": MAX_MS,
        "output_dir": str(OUT_DIR),
        "eligible_count": len(eligible),
        "skipped_duration": skipped_duration,
        "skipped_other": skipped_other,
        "downloads": [],
    }

    for i, entry in enumerate(eligible, 1):
        track = entry["track"]
        tid = track["id"]
        title = track.get("title") or f"track_{tid}"
        user = track.get("user") or {}
        artist = user.get("username") or user.get("full_name") or "Unknown Artist"
        fname = sanitize_filename(f"{artist} - {title}") + ".mp3"
        dest = OUT_DIR / fname
        if dest.exists() and dest.stat().st_size > 10_000:
            print(f"[{i}/{len(eligible)}] skip exists {fname}")
            manifest["downloads"].append({"id": tid, "file": str(dest), "status": "exists"})
            continue

        picked = pick_transcoding(track)
        if not picked:
            print(f"[{i}/{len(eligible)}] no transcoding for {title}")
            manifest["downloads"].append({"id": tid, "title": title, "status": "no_transcoding"})
            continue
        tr, trans_url = picked
        protocol = (tr.get("format") or {}).get("protocol") or ""

        try:
            print(f"[{i}/{len(eligible)}] {artist} — {title}")
            permalink = track.get("permalink_url") or ""
            if protocol == "progressive":
                stream_url = resolve_mp3_url(trans_url)
                download_file(stream_url, dest)
            elif permalink:
                download_ytdlp(permalink, dest)
            else:
                stream_url = resolve_mp3_url(trans_url)
                download_hls_to_mp3(stream_url, dest)
            tag_mp3(dest, title=title, artist=artist)
            manifest["downloads"].append(
                {"id": tid, "file": str(dest), "status": "ok", "preset": tr.get("preset")}
            )
        except urllib.error.HTTPError as e:
            print(f"  HTTP error {e.code} for track {tid}")
            manifest["downloads"].append({"id": tid, "title": title, "status": f"http_{e.code}"})
        except Exception as e:
            print(f"  failed: {e}")
            manifest["downloads"].append({"id": tid, "title": title, "status": str(e)})
        time.sleep(0.3)

    playlist = OUT_DIR.parent / "likes_2-10min.m3u"
    lines = ["#EXTM3U"]
    for p in sorted(OUT_DIR.glob("*.mp3")):
        lines.append(f"#EXTINF:-1,{p.stem}")
        lines.append(p.name)
    playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")

    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Done. Files in {OUT_DIR}")
    print(f"Playlist: {playlist}")
    print(f"Manifest: {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
