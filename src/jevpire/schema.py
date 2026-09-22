"""Canonical types for Jevpire.

Every acquisition backend produces `PitchRecord`. Nothing downstream — ground truth, Jev
prompting, baselines, evaluation — is permitted to read a raw API field. See PLAN.md §4.2.

The single most important invariant in this module: a pitch's location is NEVER stored as a
bare (x, z) pair. It is stored as the nine-parameter trajectory and evaluated at an
explicitly named plane. Both MLB sources publish an (x, z) but on *different* planes, and
mixing them silently corrupts every close call (PLAN.md §3.1).
"""

from __future__ import annotations

import math
from datetime import date, datetime
from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0.0"

# --- field geometry, in feet ---------------------------------------------------------
PLATE_FRONT_Y = 17.0 / 12.0       # 1.41667 -- front edge of home plate
PLATE_MIDDLE_Y = 8.5 / 12.0       # 0.70833 -- where ABS adjudicates, 2026+
PLATE_HALF_WIDTH = 8.5 / 12.0     # 0.70833 -- half of the 17" plate
BALL_RADIUS = 1.45 / 12.0         # 0.12083 -- any part of the ball touching = strike
RUBBER_Y = 60.5
TRAJECTORY_ANCHOR_Y = 50.0        # the plane the fit is referenced to

# ABS zone as a fraction of measured batter height
ABS_ZONE_TOP_FRAC = 0.535
ABS_ZONE_BOT_FRAC = 0.270
ABS_ZONE_RATIO = ABS_ZONE_TOP_FRAC / ABS_ZONE_BOT_FRAC   # 1.98148...


class Call(StrEnum):
    BALL = "BALL"
    STRIKE = "STRIKE"

    @property
    def flipped(self) -> Call:
        return Call.STRIKE if self is Call.BALL else Call.BALL


class AttributionTier(StrEnum):
    """How a challenged pitch was identified. See PLAN.md §6.5."""

    REVIEW_PAUSE = "tier1:unique-review-pause"
    LAST_CALLED = "tier2:last-called-pitch"


class Provenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str                       # "statsapi" | "savant" | "cv:v1"
    fetched_at: datetime
    schema_version: str = SCHEMA_VERSION
    source_hash: str | None = None    # hash of the raw payload, for reproducibility


class PitchTrajectory(BaseModel):
    """Constant-acceleration fit, ANCHORED AT y = 50 ft -- not at the release point.

    t = 0 is the moment the ball crosses y = 50. Release happens at y ~ 53-55 ft, which is
    at NEGATIVE t. Getting this wrong puts the plate location out by ~0.45 ft, which is
    wider than the ball (see DATA_DICTIONARY.md §2).
    """

    model_config = ConfigDict(frozen=True)

    x0: float
    y0: float = Field(description="Anchor plane; always ~50.0 for MLB sources")
    z0: float
    vx0: float
    vy0: float = Field(description="Negative: the ball travels toward decreasing y")
    vz0: float
    ax: float
    ay: float = Field(description="Positive: drag opposes motion in -y")
    az: float

    #: The fit is a parabola in t, so it "reaches" absurd y values at absurd times.
    #: A real pitch is airborne for <0.6 s; anything beyond this is outside the model.
    MAX_PLAUSIBLE_T: ClassVar[float] = 1.0

    def time_to(self, y: float) -> float:
        """Seconds from the y=50 anchor until the ball reaches plane `y`.

        Raises if the plane is outside the modelled flight. The quadratic is only a valid
        description of the ball between release and the plate; extrapolating it returns a
        mathematically real but physically meaningless root, which is worse than an error.
        """
        a = 0.5 * self.ay
        b = self.vy0
        c = self.y0 - y
        disc = b * b - 4 * a * c
        if disc < 0:
            raise ValueError(f"trajectory never reaches y={y}")
        root = math.sqrt(disc)
        r1 = (-b - root) / (2 * a)
        r2 = (-b + root) / (2 * a)
        t = r1 if abs(r1) < abs(r2) else r2
        if abs(t) > self.MAX_PLAUSIBLE_T:
            raise ValueError(
                f"y={y} is outside the modelled flight (t={t:.3f}s); the "
                "constant-acceleration fit is only valid between release and the plate"
            )
        return t

    def location_at_time(self, t: float) -> tuple[float, float, float]:
        return (
            self.x0 + self.vx0 * t + 0.5 * self.ax * t * t,
            self.y0 + self.vy0 * t + 0.5 * self.ay * t * t,
            self.z0 + self.vz0 * t + 0.5 * self.az * t * t,
        )

    def location_at_plane(self, y: float) -> tuple[float, float]:
        """(x, z) where the ball crosses plane `y`. The only sanctioned way to get location."""
        x, _, z = self.location_at_time(self.time_to(y))
        return x, z

    def speed_at_time(self, t: float) -> float:
        """Speed in mph."""
        vx = self.vx0 + self.ax * t
        vy = self.vy0 + self.ay * t
        vz = self.vz0 + self.az * t
        return math.sqrt(vx * vx + vy * vy + vz * vz) * 0.6818181818

    @classmethod
    def from_release_anchored(
        cls,
        *,
        release_x: float, release_y: float, release_z: float,
        vx0: float, vy0: float, vz0: float,
        ax: float, ay: float, az: float,
    ) -> PitchTrajectory:
        """Build from Savant's parameterisation, which gives the RELEASE point.

        Savant publishes release_pos_x/y/z at the actual release plane (~53-55 ft) while the
        velocity/acceleration terms are referenced to y=50. Back-solve to the y=50 anchor so
        the fit is self-consistent. Verified to reproduce Savant's own plate_x/plate_z to
        0.005 ft; skipping this step costs 0.45 ft.
        """
        a = 0.5 * ay
        disc = vy0 * vy0 - 4 * a * (TRAJECTORY_ANCHOR_Y - release_y)
        root = math.sqrt(disc)
        r1 = (-vy0 - root) / (2 * a)
        r2 = (-vy0 + root) / (2 * a)
        t_rel = r1 if abs(r1) < abs(r2) else r2   # negative: release precedes y=50
        return cls(
            x0=release_x - (vx0 * t_rel + 0.5 * ax * t_rel * t_rel),
            y0=TRAJECTORY_ANCHOR_Y,
            z0=release_z - (vz0 * t_rel + 0.5 * az * t_rel * t_rel),
            vx0=vx0, vy0=vy0, vz0=vz0, ax=ax, ay=ay, az=az,
        )


class StrikeZone(BaseModel):
    """The 2026 ABS zone: a flat 2D rectangle at the middle of the plate.

    Derived from measured batter height, ignoring stance. Verified constant per batter for
    the whole season (407/407 batters, 0.000 in drift).
    """

    model_config = ConfigDict(frozen=True)

    top: float      # ft above ground
    bottom: float

    HALF_WIDTH: ClassVar[float] = PLATE_HALF_WIDTH
    BALL_RADIUS: ClassVar[float] = BALL_RADIUS
    PLANE_Y: ClassVar[float] = PLATE_MIDDLE_Y

    @property
    def implied_height_ft(self) -> float:
        return self.top / ABS_ZONE_TOP_FRAC

    @property
    def ratio(self) -> float:
        """Should equal 1.98148 if this really is the height-derived ABS zone."""
        return self.top / self.bottom


class VideoRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    play_id: str
    local_path: str | None = None
    bytes: int | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    duration_s: float | None = None


class ChallengeRecord(BaseModel):
    """An ABS challenge. Only ~0.3% of pitches have one (PLAN.md §3.3)."""

    model_config = ConfigDict(frozen=True)

    is_overturned: bool
    challenger_id: int | None = None
    challenger_name: str | None = None
    challenge_team_id: int | None = None
    attribution_tier: AttributionTier
    review_pause_s: float | None = None


class PitchRecord(BaseModel):
    """The only type downstream code is allowed to consume."""

    model_config = ConfigDict(frozen=True)

    # --- identity ---
    pitch_id: str
    game_pk: int
    at_bat_number: int
    pitch_number: int
    play_id: str | None = None
    game_date: date

    # --- participants ---
    pitcher_id: int
    pitcher_name: str | None = None
    pitcher_hand: Literal["L", "R"]
    batter_id: int
    batter_name: str | None = None
    batter_stand: Literal["L", "R"]
    catcher_id: int | None = None
    hp_umpire_id: int | None = None
    hp_umpire_name: str | None = None

    # --- physics ---
    trajectory: PitchTrajectory
    zone: StrikeZone
    release_speed: float | None = None
    release_extension: float | None = None
    spin_rate: float | None = None
    spin_axis: float | None = None
    pitch_type: str | None = None
    pitch_name: str | None = None
    plate_time: float | None = None

    # --- game state, all strictly pre-pitch ---
    balls: int
    strikes: int
    outs: int
    inning: int
    is_top: bool
    runner_on_1b: bool = False
    runner_on_2b: bool = False
    runner_on_3b: bool = False
    score_bat: int | None = None
    score_fld: int | None = None
    venue_id: int | None = None
    venue_name: str | None = None

    # --- outcome ---
    description: str
    is_taken: bool
    umpire_call: Call | None = None       # reconstructed pre-challenge
    abs_call: Call | None = None          # only on challenged pitches
    challenge: ChallengeRecord | None = None

    # --- labels (derived; never read from a source) ---
    ground_truth: Call
    margin_in: float

    video: VideoRef | None = None
    provenance: Provenance

    @property
    def plate_location(self) -> tuple[float, float]:
        """(x, z) at the ABS plane. Derived, never stored."""
        return self.trajectory.location_at_plane(StrikeZone.PLANE_Y)

    @property
    def is_close_call(self) -> bool:
        """Within 1 inch of a zone edge -- 11% of taken pitches, where umpires hit 66%."""
        return abs(self.margin_in) < 1.0


def make_pitch_id(game_pk: int, at_bat_number: int, pitch_number: int) -> str:
    return f"{game_pk}-{at_bat_number}-{pitch_number}"
