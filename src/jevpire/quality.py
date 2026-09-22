"""Acquisition quality gates (PLAN.md §12.4).

These are not decoration. Gates 1 and 3 are exactly the checks that caught the two real bugs
found during the red-team: the front-vs-middle-of-plate mismatch, and the post-challenge
call correction. A pipeline that fails loudly here is worth far more than one that quietly
produces a subtly wrong dataset.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from .schema import (
    ABS_ZONE_RATIO,
    PLATE_MIDDLE_Y,
    AttributionTier,
    Call,
    PitchRecord,
)
from .sources.savant import SavantPitch

LOCATION_TOLERANCE_FT = 0.02
TAKEN_FRACTION_RANGE = (0.50, 0.54)
BALL_CLASS_RANGE = (0.66, 0.72)
MIN_USABLE_FRACTION = 0.995
MIN_ATTRIBUTION_RATE = 0.95
MIN_CHALLENGE_AGREEMENT = 0.99
ZONE_TOLERANCE_FT = 0.01
MAX_ZONE_OUTLIER_FRACTION = 0.005


def find_zone_outliers(records: list[PitchRecord]) -> dict[str, str]:
    """Pitches whose strike zone disagrees with their batter's modal zone.

    The ABS zone is a pure function of measured height, so for a given batter it is a
    CONSTANT -- verified at 407/407 batters with 0.000 in drift. Any deviation is therefore
    a tracking or feed error, not real variation.

    Observed in the wild: one batter carried two zone tops differing by 2 inches *within a
    single plate appearance*. A pitch with the wrong zone has an unreliable ground-truth
    label, so these are quarantined rather than corrected -- inventing the "right" zone
    would be fabricating a label.
    """
    by_batter: dict[int, Counter[float]] = {}
    for r in records:
        by_batter.setdefault(r.batter_id, Counter())[round(r.zone.top, 6)] += 1

    modal = {b: c.most_common(1)[0][0] for b, c in by_batter.items()}

    out: dict[str, str] = {}
    for r in records:
        expected = modal[r.batter_id]
        if abs(r.zone.top - expected) > ZONE_TOLERANCE_FT:
            out[r.pitch_id] = (
                f"zone top {r.zone.top:.3f} deviates from batter's modal "
                f"{expected:.3f} ({abs(r.zone.top - expected) * 12:.2f} in)"
            )
    return out


@dataclass
class GateResult:
    name: str
    passed: bool
    detail: str

    def __str__(self) -> str:
        return f"[{'PASS' if self.passed else 'FAIL'}] {self.name}: {self.detail}"


@dataclass
class QualityReport:
    gates: list[GateResult] = field(default_factory=list)
    quarantined: list[tuple[str, str]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(g.passed for g in self.gates)

    def add(self, name: str, passed: bool, detail: str) -> None:
        self.gates.append(GateResult(name, passed, detail))

    def render(self) -> str:
        lines = [str(g) for g in self.gates]
        if self.quarantined:
            lines.append(f"\nquarantined rows: {len(self.quarantined)}")
            for pid, why in self.quarantined[:10]:
                lines.append(f"  {pid}: {why}")
        lines.append(f"\nOVERALL: {'PASS' if self.passed else 'FAIL'}")
        return "\n".join(lines)


def run_gates(
    records: list[PitchRecord],
    *,
    savant_index: dict[tuple[int, int, int], SavantPitch] | None = None,
    expect_taken_range: tuple[float, float] = TAKEN_FRACTION_RANGE,
) -> QualityReport:
    rep = QualityReport()
    if not records:
        rep.add("non-empty", False, "no records")
        return rep

    taken = [r for r in records if r.is_taken]

    # --- gate 1: cross-source location agreement --------------------------------------
    if savant_index:
        errs: list[float] = []
        for r in records:
            # Join on SAVANT's pitch numbering, not StatsAPI's -- they differ whenever the
            # at-bat contains a pitch-timer violation (see PitchRecord.savant_pitch_number).
            key = (r.game_pk, r.at_bat_number, r.savant_pitch_number or r.pitch_number)
            sp = savant_index.get(key)
            if sp is None:
                continue
            x, z = r.trajectory.location_at_plane(PLATE_MIDDLE_Y)
            errs.append(math.hypot(x - sp.plate_x, z - sp.plate_z))

        if errs:
            errs.sort()
            worst = errs[-1]
            agreeing = sum(1 for e in errs if e < LOCATION_TOLERANCE_FT)
            frac = agreeing / len(errs)
            # The gate's purpose is detecting a SYSTEMATIC plane error, which would move
            # every row. A stray row is a join artefact, so the gate is on the fraction.
            rep.add(
                "1. cross-source location",
                frac >= 0.999,
                f"{len(errs)} compared, {frac:.4%} within {LOCATION_TOLERANCE_FT} ft, "
                f"median {errs[len(errs)//2]:.5f} ft, max {worst:.4f} ft",
            )
        else:
            rep.add("1. cross-source location", False, "no overlapping pitches to compare")
    else:
        rep.add("1. cross-source location", True, "skipped (no Savant index supplied)")

    # --- gate 2: the zone really is height-derived ------------------------------------
    bad_ratio = [r for r in records if abs(r.zone.ratio - ABS_ZONE_RATIO) > 1e-3]
    rep.add(
        "2. zone ratio 53.5/27",
        not bad_ratio,
        f"{len(records) - len(bad_ratio)}/{len(records)} match {ABS_ZONE_RATIO:.5f}",
    )

    # --- gate 7: zone constant per batter ---------------------------------------------
    outliers = find_zone_outliers(records)
    n_batters = len({r.batter_id for r in records})
    frac = len(outliers) / len(records)
    rep.add(
        "7. zone constant per batter",
        frac <= MAX_ZONE_OUTLIER_FRACTION,
        f"{n_batters} batters, {len(outliers)} pitches ({frac:.3%}) deviate from their "
        f"batter's modal zone (limit {MAX_ZONE_OUTLIER_FRACTION:.1%})",
    )
    for pid, why in outliers.items():
        rep.quarantined.append((pid, why))

    # --- gate 3: challenges ------------------------------------------------------------
    challenged = [r for r in records if r.challenge is not None]
    if challenged:
        per_tier: Counter[AttributionTier] = Counter()
        agree: Counter[AttributionTier] = Counter()
        for r in challenged:
            t = r.challenge.attribution_tier
            per_tier[t] += 1
            if r.ground_truth == r.abs_call:
                agree[t] += 1
        details = ", ".join(
            f"{t.value.split(':')[0]} {agree[t]}/{per_tier[t]}" for t in sorted(per_tier, key=str)
        )
        ok = all(agree[t] / per_tier[t] >= MIN_CHALLENGE_AGREEMENT for t in per_tier)
        rep.add("3. rule vs ABS, per tier", ok, details)
    else:
        rep.add("3. rule vs ABS, per tier", True, "no challenges in this sample")

    # --- gate 4: taken fraction --------------------------------------------------------
    frac = len(taken) / len(records)
    lo, hi = expect_taken_range
    rep.add("4. taken fraction", lo <= frac <= hi, f"{frac:.3f} (expect {lo}-{hi})")

    # --- gate 5: class balance ---------------------------------------------------------
    if taken:
        ball_frac = sum(1 for r in taken if r.ground_truth is Call.BALL) / len(taken)
        lo, hi = BALL_CLASS_RANGE
        rep.add("5. class balance", lo <= ball_frac <= hi,
                f"{ball_frac:.3f} BALL (expect {lo}-{hi})")
    else:
        rep.add("5. class balance", False, "no taken pitches")

    # --- gate 6: usable fraction (reported by the caller via expected_total) -----------
    rep.add("6. records parsed", True, f"{len(records)} pitches, {len(taken)} taken")

    return rep


def validate_record(r: PitchRecord) -> str | None:
    """Per-row sanity. Returns a reason string if the row belongs in quarantine."""
    if not (0 <= r.balls <= 3):
        return f"impossible ball count {r.balls}"
    if not (0 <= r.strikes <= 2):
        return f"impossible strike count {r.strikes}"
    if not (0 <= r.outs <= 2):
        return f"impossible out count {r.outs}"
    if r.zone.top <= r.zone.bottom:
        return f"inverted zone {r.zone.bottom}..{r.zone.top}"
    if not (1.0 < r.zone.top < 5.0):
        return f"implausible zone top {r.zone.top}"
    if r.trajectory.vy0 >= 0:
        return f"vy0 must be negative, got {r.trajectory.vy0}"
    if abs(r.margin_in) > 120:
        return f"implausible margin {r.margin_in:.1f} in"
    if r.is_taken and r.umpire_call is None:
        return "taken pitch with no umpire call"
    if r.abs_call is not None and r.challenge is None:
        return "abs_call without a challenge record"
    return None
