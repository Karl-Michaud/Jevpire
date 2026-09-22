"""Red-team R2 (round 2): a better ABS-challenge attribution rule, measured honestly.

Round 1 used "the pitch with a >15 s review pause" and attributed only 48.5% of
challenges, which made the ground-truth validation a selected subsample.

Diagnosis of the dropped cases showed two distinct failure modes:
  (a) NO pitch carries the review pause -- the pause simply is not recorded.
      In every inspected case the challenged pitch was the LAST called pitch of the play.
  (b) TWO pitches carry a 27.0 s pause -- more than one challenge happened in that
      at-bat, but the API stores only ONE reviewDetails object. Genuinely ambiguous.

This script implements a tiered rule and reports coverage AND agreement per tier, so the
validation set is explicit about how each pitch was identified.

Run:  uv run python scripts/redteam_challenges_v2.py
"""

from __future__ import annotations

import math
import sys
from collections import Counter
from datetime import datetime

import httpx

HALF_WIDTH = 0.70833
BALL_RADIUS = 0.12083
ABS_PLANE_Y = 0.70833     # middle of plate -- ABS adjudication plane
REVIEW_GAP_S = 15.0
CALLED = ("B", "C", "*B")

UA = {"User-Agent": "Mozilla/5.0 (jevpire research; non-commercial)"}
client = httpx.Client(headers=UA, timeout=120.0)


def schedule(date: str) -> list[int]:
    r = client.get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}")
    r.raise_for_status()
    dates = r.json().get("dates", [])
    return [g["gamePk"] for g in dates[0]["games"]
            if g["status"]["detailedState"] == "Final"] if dates else []


def location_at(c: dict, y_target: float) -> tuple[float, float]:
    a, b, cc = 0.5 * c["aY"], c["vY0"], c["y0"] - y_target
    disc = b * b - 4 * a * cc
    r1 = (-b - math.sqrt(disc)) / (2 * a)
    r2 = (-b + math.sqrt(disc)) / (2 * a)
    t = r1 if abs(r1) < abs(r2) else r2
    return (c["x0"] + c["vX0"] * t + 0.5 * c["aX"] * t * t,
            c["z0"] + c["vZ0"] * t + 0.5 * c["aZ"] * t * t)


def geometric_call(ev: dict) -> str:
    pd = ev["pitchData"]
    x, z = location_at(pd["coordinates"], ABS_PLANE_Y)
    ok = (abs(x) <= HALF_WIDTH + BALL_RADIUS
          and z <= pd["strikeZoneTop"] + BALL_RADIUS
          and z >= pd["strikeZoneBottom"] - BALL_RADIUS)
    return "C" if ok else "B"


def dur(ev: dict) -> float:
    s = datetime.fromisoformat(ev["startTime"].replace("Z", "+00:00"))
    e = datetime.fromisoformat(ev["endTime"].replace("Z", "+00:00"))
    return (e - s).total_seconds()


def norm(code: str) -> str:
    return "B" if code in ("B", "*B") else code


def attribute(play: dict) -> tuple[dict | None, str]:
    """Return (challenged_pitch_event, tier) or (None, reason)."""
    pitches = [e for e in play.get("playEvents", [])
               if e.get("isPitch")
               and e.get("pitchData", {}).get("coordinates", {}).get("y0") is not None]
    called = [e for e in pitches if e["details"]["call"]["code"] in CALLED]
    if not called:
        return None, "drop:no-called-pitch"

    paused = [e for e in called if dur(e) > REVIEW_GAP_S]
    if len(paused) == 1:
        return paused[0], "tier1:unique-review-pause"
    if len(paused) > 1:
        return None, "drop:multiple-challenges-one-record"
    return called[-1], "tier2:last-called-pitch"


def main() -> int:
    dates = ["2026-04-20", "2026-04-21", "2026-05-12", "2026-06-15", "2026-06-16",
             "2026-07-08", "2026-08-10", "2026-08-11", "2026-09-05", "2026-09-06"]
    games: list[int] = []
    for d in dates:
        games.extend(schedule(d))
    print(f"games: {len(games)} across {len(dates)} dates\n")

    tiers: Counter[str] = Counter()
    agree: Counter[str] = Counter()
    total: Counter[str] = Counter()
    overturned = 0
    mismatches: list[str] = []

    for i, pk in enumerate(games, 1):
        if i % 25 == 0:
            print(f"  ...{i}/{len(games)}", file=sys.stderr)
        try:
            f = client.get(f"https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live").json()
        except Exception:  # noqa: BLE001
            tiers["drop:fetch-failed"] += 1
            continue

        for play in f["liveData"]["plays"]["allPlays"]:
            rd = play.get("reviewDetails")
            if not rd or rd.get("reviewType") != "MJ":
                continue
            ev, tier = attribute(play)
            tiers[tier] += 1
            if ev is None:
                continue
            total[tier] += 1
            if rd.get("isOverturned"):
                overturned += 1
            stored = norm(ev["details"]["call"]["code"])
            if geometric_call(ev) == stored:
                agree[tier] += 1
            else:
                pd = ev["pitchData"]
                x, z = location_at(pd["coordinates"], ABS_PLANE_Y)
                mismatches.append(
                    f"game {pk} ab{play['about']['atBatIndex']} [{tier}] stored={stored} "
                    f"geom={geometric_call(ev)} x={x:+.3f} z={z:.3f} "
                    f"zone=[{pd['strikeZoneBottom']:.3f},{pd['strikeZoneTop']:.3f}]"
                )

    n_mj = sum(tiers.values())
    print("\n=== R2 round 2: attribution coverage ===")
    for k, v in sorted(tiers.items()):
        print(f"  {k:<42} {v:>4}  ({100*v/n_mj:5.1f}%)")
    attributed = sum(total.values())
    print(f"  {'TOTAL MJ plays':<42} {n_mj:>4}")
    print(f"  {'attributed':<42} {attributed:>4}  ({100*attributed/n_mj:5.1f}%)")

    print("\n=== agreement of geometric rule with stored (ABS-corrected) call ===")
    for k in sorted(total):
        print(f"  {k:<42} {agree[k]:>4}/{total[k]:<4} = {100*agree[k]/total[k]:6.1f}%")
    if attributed:
        tot_a = sum(agree.values())
        print(f"  {'OVERALL':<42} {tot_a:>4}/{attributed:<4} = {100*tot_a/attributed:6.1f}%")
        print(f"\n  overturn rate: {100*overturned/attributed:.1f}%")

    if mismatches:
        print(f"\n  {len(mismatches)} mismatches:")
        for m in mismatches[:25]:
            print(f"    - {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
