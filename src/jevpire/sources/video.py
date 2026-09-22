"""Pitch video: play_id -> mp4 on local disk.

Collected in v1 even though v1 does not use it. Part 2 and Part 3 are deferred, not
cancelled, and every option on the table consumes the same clips -- so gathering now costs
one pass while skipping it costs the whole pass again later (PLAN.md §1.1).

Two operational notes learned the hard way:
  - the mp4 host returns 403 without a Referer pointing at baseballsavant
  - the signed mp4 URLs expire, so the page must be re-resolved rather than the link cached
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..schema import VideoRef
from .base import CachedFetcher

PAGE_URL = "https://baseballsavant.mlb.com/sporty-videos?playId={play_id}"
SOURCE_RE = re.compile(r'<source[^>]*src="([^"]+\.mp4)"', re.IGNORECASE)
MP4_HEADERS = {"Referer": "https://baseballsavant.mlb.com/", "Accept": "*/*"}


@dataclass(frozen=True)
class VideoResult:
    play_id: str
    ref: VideoRef | None
    status: str          # "downloaded" | "cached" | "no-source" | "error: ..."


class VideoSource:
    name = "savant-video"

    def __init__(self, cache_dir: Path, video_dir: Path) -> None:
        self.fetcher = CachedFetcher(Path(cache_dir) / "video-pages")
        self.video_dir = Path(video_dir)
        self.video_dir.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        self.fetcher.close()

    def _dest(self, play_id: str) -> Path:
        # shard by first two hex chars; 10k files in one directory is miserable on Windows
        return self.video_dir / play_id[:2] / f"{play_id}.mp4"

    def fetch(self, play_id: str, *, probe: bool = True) -> VideoResult:
        dest = self._dest(play_id)
        if dest.exists() and dest.stat().st_size > 0:
            return VideoResult(play_id, self._ref(play_id, dest, probe), "cached")

        try:
            page = self.fetcher.get_text(
                PAGE_URL.format(play_id=play_id), f"{play_id[:2]}/{play_id}.html"
            )
        except Exception as exc:  # noqa: BLE001
            return VideoResult(play_id, None, f"error: page fetch: {exc}")

        m = SOURCE_RE.search(page)
        if not m:
            # Savant genuinely has no clip for some pitches (spring training, very recent games)
            return VideoResult(play_id, None, "no-source")

        try:
            self.fetcher.get_bytes(m.group(1), dest, headers=MP4_HEADERS)
        except Exception as exc:  # noqa: BLE001
            return VideoResult(play_id, None, f"error: download: {exc}")

        return VideoResult(play_id, self._ref(play_id, dest, probe), "downloaded")

    def _ref(self, play_id: str, dest: Path, probe: bool) -> VideoRef:
        meta: dict = {}
        if probe:
            meta = probe_video(dest)
        return VideoRef(
            play_id=play_id,
            local_path=str(dest),
            bytes=dest.stat().st_size,
            width=meta.get("width"),
            height=meta.get("height"),
            fps=meta.get("fps"),
            duration_s=meta.get("duration_s"),
        )


def probe_video(path: Path) -> dict:
    """ffprobe metadata. Returns {} if ffprobe is unavailable or the file is unreadable."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate,duration,nb_frames",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return {}

    try:
        streams = json.loads(out).get("streams", [])
    except json.JSONDecodeError:
        return {}
    if not streams:
        return {}

    s = streams[0]
    fps = None
    if rate := s.get("r_frame_rate"):
        try:
            num, den = rate.split("/")
            fps = float(num) / float(den) if float(den) else None
        except (ValueError, ZeroDivisionError):
            fps = None

    duration = None
    if s.get("duration") is not None:
        try:
            duration = float(s["duration"])
        except (TypeError, ValueError):
            duration = None

    return {
        "width": s.get("width"),
        "height": s.get("height"),
        "fps": fps,
        "duration_s": duration,
    }
