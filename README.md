# SoundCloud likes → Sony Walkman

Pipeline to sync your SoundCloud **track likes** to a Sony Walkman: download MP3s (duration filter), tag and stage as `Artist/Album/track.mp3`, then copy over **MTP** on Linux with resume and remount logic.

Tested on Walkman over GVFS (`gio copy`). USB **mass storage (MSC)** is still the most reliable option if your model supports it—copy `WALKMAN_STAGING/MUSIC/` manually.

## Requirements

- Python 3.10+
- `mutagen` (`pip install -r requirements.txt`)
- `yt-dlp` on `PATH` (fallback when SoundCloud only serves HLS)
- Linux with GVFS MTP (`gio`), Walkman unlocked, USB file transfer mode

## Configuration

Copy `.env.example` and export variables (never commit tokens):

```bash
export SC_OAUTH_TOKEN='OAuth 2-...'   # browser devtools on soundcloud.com
export SC_USER_ID='12345678'            # your numeric user id
```

Optional: `SC_CLIENT_ID`, `SC_MIN_DURATION_MS`, `SC_MAX_DURATION_MS`, `MTP_REMOUNT_EVERY`, `WALKMAN_MUSIC_ROOT`.

## Workflow

### 1. Download likes (2–10 min, exclusive)

```bash
python3 download_likes.py
```

Writes flat MP3s to `MUSIC/`, `manifest.json`, and optional `likes_2-10min.m3u`. Re-runs skip existing files.

### 2. Stage for Walkman (ID3 + embedded cover)

```bash
python3 transfer_walkman.py
```

Builds `WALKMAN_STAGING/MUSIC/Artist/Album/*.mp3`. Covers are embedded in tags; `folder.jpg` may exist locally but is **not** synced to the device.

Staging only (no MTP):

```bash
python3 transfer_walkman.py   # without WALKMAN_SYNC_ONLY; use step 3 for copy
```

### 3. Sync to Walkman (recommended)

```bash
python3 mtp_sync.py --remount
```

- Rebuilds MTP-safe paths under `/tmp/walkman-mtp-export/MUSIC` (sanitized names, `.mp3` preserved when truncating)
- Copies **MP3 only** via `gio copy`, verifies file size, remounts periodically
- Resumes with `mtp_sync_state.json`

After errors or unplug:

```bash
python3 mtp_sync.py --remount --no-export   # reuse export tree
```

See [MTP.md](MTP.md) for failure modes and tips.

### 4. Optional: prune device

Remove on the Walkman anything not in staging:

```bash
DRY_RUN=1 python3 prune_walkman.py   # preview
python3 prune_walkman.py
```

## Layout

| Script | Role |
|--------|------|
| `download_likes.py` | SoundCloud API + download |
| `transfer_walkman.py` | Tags, album layout, optional legacy sync |
| `export_mtp_tree.py` | MTP-safe path mirror |
| `mtp_sync.py` | **Main device sync** |
| `mtp_connection.py` | Discover/remount GVFS MTP URI |
| `mtp_paths.py` | Safe path segments for libmtp |
| `prune_walkman.py` | Delete extras on device |

## License

MIT — use at your own risk; respect SoundCloud and rights holders when downloading.
