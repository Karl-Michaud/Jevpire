"""Acquisition backends. Each returns `list[PitchRecord]` and nothing else."""

from .base import CachedFetcher, PitchSource, payload_hash
from .statsapi import StatsAPISource

__all__ = ["CachedFetcher", "PitchSource", "StatsAPISource", "payload_hash"]
