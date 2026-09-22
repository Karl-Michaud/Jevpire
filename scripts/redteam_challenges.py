"""Red-team check R2: is the ABS-challenge identification lossy or selective?

The 123/123 ground-truth validation is only meaningful if we find EVERY challenge,
not just the convenient ones. This script counts:
  - how many plays carry reviewType == "MJ"
  - how many of those we can attribute to a specific pitch
  - what we drop, and why

Run:  uv run python scripts/redteam_challenges.py
"""

from __future__ import annotations

import math
import sys
from datetime import datetime

import httpx

HALF_WIDTH = 0.70833
BALL_RADIUS = 0.12083
ABS_PLANE_Y = 0.70833
REVIEW_GAP_S = 15.0  # a challenged pitch shows a review pause far longer than a normal one

UA = {"User-Agent": "Mozilla/5.0 (jevpire research; non-commercial)"}
client = httpx.Client(headers=UA, timeout=120.0)


def schedule(date: str) -> list[int]:
    r = client.get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}")
    r.raise_for_status()
    dates = r.json().get("dates", [])
    if not dates:
        return []
    return [
        g["gamePk"] for g in dates[0]["games"]
        if g["status"]["detailedState"] == "Final"
    ]


def feed(game_pk: int) -> dict:
    r = client.get(f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live")
    r.raise_for_status()
    return r.json()


def location_at(c: dict, y_target: float) -> tuple[float, float]:
    """Solve the constant-acceleration fit (anchored at y=50) for position at plane y."""
    a, b, cc = 0.5 * c["aY"], c["vY0"], c["y0"] - y_target
    disc = b * b - 4 * a * cc
    r1 = (-b - math.sqrt(disc)) / (2 * a)
    r2 = (-b + math.sqrt(disc)) / (2 * a)
    t = r1 if abs(r1) < abs(r2) else r2
    x = c["x0"] + c["vX0"] * t + 0.5 * c["aX"] * t * t
    z = c["z0"] + c["vZ0"] * t + 0.5 * c["aZ"] * t * t
    return x, z


def geometric_call(ev: dict) -> str:
    c = ev["pitchData"]["coordinates"]
    x, z = location_at(c, ABS_PLANE_Y)
    top = ev["pitchData"]["strikeZoneTop"]
    bot = ev["pitchData"]["strikeZoneBottom"]
    strike = (
        abs(x) <= HALF_WIDTH + BALL_RADIUS
        and z <= top + BALL_RADIUS
        and z >= bot - BALL_RADIUS
    )
    return "C" if strike else "B"


def dur(ev: dict) -> float:
    s = datetime.fromisoformat(ev["startTime"].replace("Z", "+00:00"))
    e = datetime.fromisoformat(ev["endTime"].replace("Z", "+00:00"))
    return (e - s).total_seconds()


def main() -> int:
    dates = [
        "2026-04-20", "2026-04-21", "2026-05-12", "2026-06-15", "2026-06-16",
        "2026-07-08", "2026-08-10", "2026-08-11", "2026-09-05", "2026-09-06",
    ]
    games: list[int] = []
    for d in dates:
        games.extend(schedule(d))
    print(f"games: {len(games)} across {len(dates)} dates spanning the season\n")

    mj_plays = 0
    attributed = 0
    dropped: list[str] = []
    agree = 0
    overturned = 0
    gaps: list[float] = []

    for i, pk in enumerate(games, 1):
        if i % 25 == 0:
            print(f"  ...{i}/{len(games)}", file=sys.stderr)
        try:
            f = feed(pk)
        except Exception as exc:  # noqa: BLE001
            dropped.append(f"game {pk}: fetch failed ({exc})")
            continue

        for play in f["liveData"]["plays"]["allPlays"]:
            rd = play.get("reviewDetails")
            if not rd or rd.get("reviewType") != "MJ":
                continue
            mj_plays += 1

            pitches = [e for e in play.get("playEvents", []) if e.get("isPitch")]
            cands = [
                e for e in pitches
                if dur(e) > REVIEW_GAP_S
                and e.get("pitchData", {}).get("coordinates", {}).get("y0") is not None
            ]
            if len(cands) != 1:
                dropped.append(
                    f"game {pk} atBat {play['about']['atBatIndex']}: "
                    f"{len(cands)} candidate pitches (of {len(pitches)})"
                )
                continue

            ev = cands[0]
            call = ev["details"]["call"]["code"]
            if call not in ("B", "C"):
                dropped.append(f"game {pk}: challenged pitch call code {call!r}")
                continue

            attributed += 1
            gaps.append(dur(ev))
            if rd.get("isOverturned"):
                overturned += 1
            if geometric_call(ev) == call:
                agree += 1

    print("\n=== R2: ABS challenge identification ===")
    print(f"  plays with reviewType == 'MJ'      : {mj_plays}")
    print(f"  attributed to a unique pitch       : {attributed}")
    print(f"  dropped                            : {len(dropped)}")
    if mj_plays:
        print(f"  attribution rate                   : {100*attributed/mj_plays:.1f}%")
    print(f"  per game                           : {mj_plays/max(len(games),1):.2f}")

    if dropped:
        print("\n  drop reasons:")
        for d in dropped[:20]:
            print(f"    - {d}")

    print("\n=== ground-truth rule vs official ABS ruling ===")
    if attributed:
        print(f"  agreement : {agree}/{attributed} = {100*agree/attributed:.1f}%")
        print(f"  overturn rate : {100*overturned/attributed:.1f}%")
    if gaps:
        gaps.sort()
        print(f"\n  review pause: min={gaps[0]:.1f}s med={gaps[len(gaps)//2]:.1f}s max={gaps[-1]:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
