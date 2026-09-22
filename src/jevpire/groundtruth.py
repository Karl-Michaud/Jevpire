"""Ground truth, and the two reconstructions that surround it.

Three things live here, all of them pure functions:

1. `ground_truth` -- the 2026 ABS zone rule. Validated against 164/164 official ABS rulings
   across 135 games spanning April-September 2026 (PLAN.md §3.2).
2. `reconstruct_umpire_call` -- MLB rewrites the stored call after an overturn, so the
   umpire's ORIGINAL call has to be recovered. Getting this backwards yields ~58% agreement
   with the rule, which is how the bug announces itself (PLAN.md §6.4).
3. `attribute_challenge` -- `reviewDetails` hangs off the at-bat, not the pitch, so the
   challenged pitch must be inferred. Tiered, 98.2% coverage (PLAN.md §6.5).
"""

from __future__ import annotations

from dataclasses import dataclass

from .schema import (
    BALL_RADIUS,
    PLATE_HALF_WIDTH,
    PLATE_MIDDLE_Y,
    AttributionTier,
    Call,
    PitchTrajectory,
    StrikeZone,
)

# A challenged pitch carries a review pause far longer than a normal pitch event
# (~27.0 s vs ~3-5 s). 15 s cleanly separates them.
REVIEW_PAUSE_MIN_S = 15.0

CALLED_CODES = frozenset({"B", "C", "*B"})
ABS_REVIEW_TYPE = "MJ"   # MF/MA = manager challenge, NH = boundary review


@dataclass(frozen=True)
class ZoneVerdict:
    call: Call
    margin_in: float
    """Signed distance to the nearest zone edge, inches. Positive = strike."""

    slack_x_in: float
    slack_top_in: float
    slack_bottom_in: float


def zone_verdict(trajectory: PitchTrajectory, zone: StrikeZone) -> ZoneVerdict:
    """Apply the ABS rule at the middle of the plate.

    A pitch is a STRIKE iff, at y = 0.70833 ft, any part of the ball touches the rectangle
    spanning [bottom, top] and +/- 8.5 in. The ball's radius expands the target on all four
    edges -- omitting it drops agreement with official rulings from 100% to 61%.
    """
    x, z = trajectory.location_at_plane(PLATE_MIDDLE_Y)

    slack_x = (PLATE_HALF_WIDTH + BALL_RADIUS) - abs(x)
    slack_top = (zone.top + BALL_RADIUS) - z
    slack_bottom = z - (zone.bottom - BALL_RADIUS)

    margin_ft = min(slack_x, slack_top, slack_bottom)
    return ZoneVerdict(
        call=Call.STRIKE if margin_ft >= 0 else Call.BALL,
        margin_in=margin_ft * 12.0,
        slack_x_in=slack_x * 12.0,
        slack_top_in=slack_top * 12.0,
        slack_bottom_in=slack_bottom * 12.0,
    )


def ground_truth(trajectory: PitchTrajectory, zone: StrikeZone) -> Call:
    return zone_verdict(trajectory, zone).call


# --- umpire / ABS call reconstruction -------------------------------------------------


def call_from_code(code: str) -> Call | None:
    """MLB pitch-result codes. Only called (taken) pitches map to a ball/strike decision."""
    if code in ("B", "*B"):
        return Call.BALL
    if code == "C":
        return Call.STRIKE
    return None


def reconstruct_calls(
    stored_code: str,
    *,
    was_challenged: bool,
    is_overturned: bool,
) -> tuple[Call | None, Call | None]:
    """Return (umpire_call, abs_call) from the stored call code.

    The stored code on a challenged pitch is ALREADY the post-challenge corrected call, so:
      - abs_call    = stored call (the machine's ruling)
      - umpire_call = stored call, flipped iff the challenge was upheld
    """
    stored = call_from_code(stored_code)
    if stored is None:
        return None, None
    if not was_challenged:
        return stored, None
    return (stored.flipped if is_overturned else stored), stored


# --- challenge attribution ------------------------------------------------------------


@dataclass(frozen=True)
class Attribution:
    index: int
    tier: AttributionTier
    review_pause_s: float | None


def attribute_challenge(
    called_pitch_durations: list[tuple[int, float]],
) -> Attribution | None:
    """Identify which pitch of an MJ-reviewed at-bat was challenged.

    `called_pitch_durations` is [(index_into_caller's_list, endTime-startTime seconds), ...]
    for the CALLED pitches of the play, in order.

    Returns None when the play is genuinely ambiguous -- more than one pitch carries a review
    pause, meaning several challenges happened in that at-bat but the API stores only one
    `reviewDetails` object. 1.8% of MJ plays; excluded rather than guessed at.
    """
    if not called_pitch_durations:
        return None

    paused = [(i, d) for i, d in called_pitch_durations if d > REVIEW_PAUSE_MIN_S]

    if len(paused) == 1:
        i, d = paused[0]
        return Attribution(index=i, tier=AttributionTier.REVIEW_PAUSE, review_pause_s=d)
    if len(paused) > 1:
        return None   # multiple challenges, one record -- irrecoverable

    i, d = called_pitch_durations[-1]
    return Attribution(index=i, tier=AttributionTier.LAST_CALLED, review_pause_s=d)
