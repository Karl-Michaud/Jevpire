"""The ground-truth rule must reproduce official ABS rulings. This is the project's gate.

If this file fails, nothing downstream is trustworthy.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from jevpire.groundtruth import (
    attribute_challenge,
    call_from_code,
    ground_truth,
    reconstruct_calls,
    zone_verdict,
)
from jevpire.schema import (
    ABS_ZONE_RATIO,
    AttributionTier,
    Call,
    PitchTrajectory,
    StrikeZone,
)

FIXTURE = Path(__file__).parent / "fixtures" / "abs_challenges.json"


@pytest.fixture(scope="module")
def challenges() -> list[dict]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data["challenges"]


def _traj(row: dict) -> PitchTrajectory:
    return PitchTrajectory(
        x0=row["x0"], y0=row["y0"], z0=row["z0"],
        vx0=row["vx0"], vy0=row["vy0"], vz0=row["vz0"],
        ax=row["ax"], ay=row["ay"], az=row["az"],
    )


# --- the headline validation ----------------------------------------------------------


def test_fixture_is_substantial(challenges):
    assert len(challenges) >= 160, "fixture shrank; regenerate it"


def test_rule_reproduces_every_official_abs_ruling(challenges):
    """164/164. The stored call on a challenged pitch IS the ABS ruling."""
    mismatches = []
    for row in challenges:
        expected = call_from_code(row["stored_call_code"])
        actual = ground_truth(_traj(row), StrikeZone(top=row["sz_top"], bottom=row["sz_bot"]))
        if actual is not expected:
            v = zone_verdict(_traj(row), StrikeZone(top=row["sz_top"], bottom=row["sz_bot"]))
            mismatches.append(
                f"game {row['game_pk']} ab{row['at_bat_index']} "
                f"[{row['attribution_tier']}] expected={expected} got={actual} "
                f"margin={v.margin_in:+.2f}in"
            )
    assert not mismatches, "\n".join(mismatches)


def test_both_attribution_tiers_agree_independently(challenges):
    """The selection-bias check: tier 2 uses a different rule on different pitches.

    If tier 1 were a biased subsample, tier 2 would diverge. It does not.
    """
    per_tier: Counter[str] = Counter()
    agree: Counter[str] = Counter()
    for row in challenges:
        tier = row["attribution_tier"]
        per_tier[tier] += 1
        expected = call_from_code(row["stored_call_code"])
        actual = ground_truth(_traj(row), StrikeZone(top=row["sz_top"], bottom=row["sz_bot"]))
        if actual is expected:
            agree[tier] += 1

    assert len(per_tier) == 2, f"expected both tiers present, got {dict(per_tier)}"
    for tier, n in per_tier.items():
        assert n >= 50, f"tier {tier} too small to be meaningful: {n}"
        assert agree[tier] == n, f"tier {tier}: {agree[tier]}/{n}"


def test_close_calls_are_present_and_correct(challenges):
    """Agreement on pitches far from the edge is easy. These are the ones that matter."""
    close = []
    for row in challenges:
        zone = StrikeZone(top=row["sz_top"], bottom=row["sz_bot"])
        v = zone_verdict(_traj(row), zone)
        if abs(v.margin_in) < 1.0:
            close.append((row, v))
    assert len(close) >= 10, f"only {len(close)} close calls; fixture may be unrepresentative"
    for row, v in close:
        assert v.call is call_from_code(row["stored_call_code"]), (
            f"close call missed: margin={v.margin_in:+.3f}in game={row['game_pk']}"
        )


def test_competing_specifications_are_worse(challenges):
    """Dropping the ball radius must degrade agreement.

    If a wrong specification scored as well, the 100% would be luck rather than evidence.
    """
    from jevpire.schema import PLATE_HALF_WIDTH, PLATE_MIDDLE_Y

    no_radius_ok = 0
    for row in challenges:
        x, z = _traj(row).location_at_plane(PLATE_MIDDLE_Y)
        strike = abs(x) <= PLATE_HALF_WIDTH and row["sz_bot"] <= z <= row["sz_top"]
        if (Call.STRIKE if strike else Call.BALL) is call_from_code(row["stored_call_code"]):
            no_radius_ok += 1

    rate = no_radius_ok / len(challenges)
    assert rate < 0.90, (
        f"the no-ball-radius rule scored {rate:.1%}; if it matches the real rule, "
        "the ball radius is not actually doing any work and §6.1 needs revisiting"
    )


def test_zone_ratio_holds_for_every_challenged_batter(challenges):
    """sz_top/sz_bot must be 53.5/27 -- proof the zone is height-derived, not operator-set."""
    for row in challenges:
        zone = StrikeZone(top=row["sz_top"], bottom=row["sz_bot"])
        assert zone.ratio == pytest.approx(ABS_ZONE_RATIO, abs=1e-3), (
            f"game {row['game_pk']}: ratio {zone.ratio:.5f}"
        )


# --- call reconstruction --------------------------------------------------------------


def test_unchallenged_pitch_has_no_abs_call():
    ump, abs_ = reconstruct_calls("C", was_challenged=False, is_overturned=False)
    assert ump is Call.STRIKE
    assert abs_ is None


def test_confirmed_challenge_leaves_umpire_call_intact():
    ump, abs_ = reconstruct_calls("C", was_challenged=True, is_overturned=False)
    assert ump is Call.STRIKE
    assert abs_ is Call.STRIKE


def test_overturned_challenge_flips_the_umpire_call():
    """The stored code is already corrected; the umpire originally said the opposite.

    Getting this backwards yields ~58% agreement with the rule -- a coin flip.
    """
    ump, abs_ = reconstruct_calls("B", was_challenged=True, is_overturned=True)
    assert abs_ is Call.BALL
    assert ump is Call.STRIKE


def test_ball_in_dirt_is_a_ball():
    assert call_from_code("*B") is Call.BALL


@pytest.mark.parametrize("code", ["S", "F", "D", "X", "W"])
def test_swung_at_pitches_have_no_call(code):
    assert call_from_code(code) is None


# --- attribution ----------------------------------------------------------------------


def test_unique_review_pause_wins():
    att = attribute_challenge([(0, 3.5), (1, 27.0), (2, 4.1)])
    assert att is not None
    assert att.index == 1
    assert att.tier is AttributionTier.REVIEW_PAUSE


def test_no_pause_falls_back_to_last_called_pitch():
    att = attribute_challenge([(0, 3.5), (1, 4.2), (2, 5.1)])
    assert att is not None
    assert att.index == 2
    assert att.tier is AttributionTier.LAST_CALLED


def test_multiple_pauses_are_ambiguous_and_excluded():
    """Several challenges in one at-bat, but the API stores only one reviewDetails."""
    assert attribute_challenge([(0, 27.0), (1, 4.0), (2, 27.0)]) is None


def test_no_called_pitches_yields_nothing():
    assert attribute_challenge([]) is None
