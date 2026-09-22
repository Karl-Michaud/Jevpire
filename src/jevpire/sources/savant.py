"""Baseball Savant backend -- used as an independent cross-check, not as the primary source.

Savant carries no `playId`, so it cannot reach video, and its `umpire`/`sv_id` columns are
empty from 2026. What it does give is a second, independently-computed middle-of-plate
location, which is exactly what is needed to catch the front-vs-middle-of-plate class of bug
(PLAN.md §12.4 gate 1).

Its parameterisation differs from StatsAPI's: the position fields are the RELEASE point, not
the y=50 anchor, so the trajectory is built via `from_release_anchored`.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

from ..schema import PitchTrajectory
from .base import CachedFetcher

CSV_URL = (
    "https://baseballsavant.mlb.com/statcast_search/csv?all=true&hfGT=R%7C"
    "&game_date_gt={d1}&game_date_lt={d2}&player_type=pitcher&type=details"
)


@dataclass(frozen=True)
class SavantPitch:
    """Just enough of a Savant row to cross-check StatsAPI."""

    game_pk: int
    at_bat_number: int
    pitch_number: int
    description: str
    plate_x: float          # Savant's own published value: MIDDLE of plate, 2026+
    plate_z: float
    sz_top: float
    sz_bot: float
    trajectory: PitchTrajectory

    @property
    def key(self) -> tuple[int, int, int]:
        return (self.game_pk, self.at_bat_number, self.pitch_number)


def _f(row: dict, key: str) -> float | None:
    v = row.get(key, "")
    if v is None or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


class SavantSource:
    name = "savant"

    REQUIRED = (
        "plate_x", "plate_z", "sz_top", "sz_bot",
        "release_pos_x", "release_pos_y", "release_pos_z",
        "vx0", "vy0", "vz0", "ax", "ay", "az",
    )

    def __init__(self, cache_dir: Path) -> None:
        self.fetcher = CachedFetcher(Path(cache_dir) / "savant")

    def close(self) -> None:
        self.fetcher.close()

    def fetch_date_range(self, d1: str, d2: str) -> list[SavantPitch]:
        text = self.fetcher.get_text(
            CSV_URL.format(d1=d1, d2=d2), f"statcast_{d1}_{d2}.csv"
        )
        out: list[SavantPitch] = []
        for row in csv.DictReader(io.StringIO(text)):
            vals = {k: _f(row, k) for k in self.REQUIRED}
            if any(v is None for v in vals.values()):
                continue   # whole-pitch tracking failure; ~0.24% of rows
            try:
                game_pk = int(float(row["game_pk"]))
                ab = int(float(row["at_bat_number"]))
                pn = int(float(row["pitch_number"]))
            except (KeyError, ValueError):
                continue

            out.append(SavantPitch(
                game_pk=game_pk,
                at_bat_number=ab,
                pitch_number=pn,
                description=row.get("description", ""),
                plate_x=vals["plate_x"],
                plate_z=vals["plate_z"],
                sz_top=vals["sz_top"],
                sz_bot=vals["sz_bot"],
                trajectory=PitchTrajectory.from_release_anchored(
                    release_x=vals["release_pos_x"],
                    release_y=vals["release_pos_y"],
                    release_z=vals["release_pos_z"],
                    vx0=vals["vx0"], vy0=vals["vy0"], vz0=vals["vz0"],
                    ax=vals["ax"], ay=vals["ay"], az=vals["az"],
                ),
            ))
        return out

    def index_by_key(self, d1: str, d2: str) -> dict[tuple[int, int, int], SavantPitch]:
        return {p.key: p for p in self.fetch_date_range(d1, d2)}
