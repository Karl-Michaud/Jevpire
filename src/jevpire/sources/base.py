"""Source protocol and shared fetch/caching machinery.

Two rules this module exists to enforce:

1. Every acquisition backend returns `list[PitchRecord]` and nothing else. Downstream code
   never learns which source it came from except via `provenance`.
2. A re-run never re-fetches. Raw payloads are archived verbatim on first fetch and read
   from disk thereafter, so derivations can be redone without touching MLB's servers.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Protocol, runtime_checkable

import httpx

from ..schema import PitchRecord

USER_AGENT = "Mozilla/5.0 (compatible; jevpire/0.1; non-commercial research)"

# Politeness. MLB publishes no rate limit for these endpoints; this is self-imposed.
MIN_INTERVAL_S = 0.15
MAX_RETRIES = 3
BACKOFF_S = 2.0


@runtime_checkable
class PitchSource(Protocol):
    name: str

    def fetch_game(self, game_pk: int) -> list[PitchRecord]: ...


def payload_hash(raw: bytes | str) -> str:
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    return hashlib.sha256(data).hexdigest()[:16]


class CachedFetcher:
    """HTTP with an on-disk archive of raw payloads, rate limiting and retries."""

    def __init__(self, cache_dir: Path, *, timeout: float = 120.0) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True
        )
        self._last_request = 0.0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> CachedFetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < MIN_INTERVAL_S:
            time.sleep(MIN_INTERVAL_S - elapsed)
        self._last_request = time.monotonic()

    def get_text(self, url: str, cache_key: str, *, headers: dict | None = None) -> str:
        path = self.cache_dir / cache_key
        if path.exists():
            return path.read_text(encoding="utf-8")

        last: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                self._throttle()
                r = self._client.get(url, headers=headers)
                r.raise_for_status()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(r.text, encoding="utf-8")
                return r.text
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt < MAX_RETRIES - 1:
                    time.sleep(BACKOFF_S * (attempt + 1))
        raise RuntimeError(f"failed after {MAX_RETRIES} attempts: {url}") from last

    def get_json(self, url: str, cache_key: str) -> dict:
        return json.loads(self.get_text(url, cache_key))

    def get_bytes(
        self, url: str, dest: Path, *, headers: dict | None = None
    ) -> tuple[int, bool]:
        """Download to `dest`. Returns (bytes, was_cached). Never re-downloads."""
        dest = Path(dest)
        if dest.exists() and dest.stat().st_size > 0:
            return dest.stat().st_size, True

        dest.parent.mkdir(parents=True, exist_ok=True)
        last: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                self._throttle()
                with self._client.stream("GET", url, headers=headers) as r:
                    r.raise_for_status()
                    tmp = dest.with_suffix(dest.suffix + ".part")
                    with tmp.open("wb") as fh:
                        for chunk in r.iter_bytes(65536):
                            fh.write(chunk)
                    tmp.replace(dest)
                return dest.stat().st_size, False
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt < MAX_RETRIES - 1:
                    time.sleep(BACKOFF_S * (attempt + 1))
        raise RuntimeError(f"download failed after {MAX_RETRIES} attempts: {url}") from last
