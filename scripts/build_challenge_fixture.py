"""Freeze real ABS challenge rulings into a test fixture.

The ground-truth rule's validation (PLAN.md §3.2) is only meaningful if it runs in CI on
every change. This pulls every attributable ABS challenge from a fixed set of dates and
writes them to tests/fixtures/abs_challenges.json.

Dates are fixed, not "recent", so the fixture is reproducible.

Run:  uv run python scripts/build_challenge_fixture.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jevpire.groundtruth import (  # noqa: E402
    ABS_REVIEW_TYPE,
    CALLED_CODES,
    attribute_challenge,
)

DATES = [
    "2026-04-20", "2026-04-21", "2026-05-12", "2026-06-15", "2026-06-16",
    "2026-07-08", "2026-08-10", "2026-08-11", "2026-09-05", "2026-09-06",
]
UA = {"User-Agent": "Mozilla/5.0 (jevpire research; non-commercial)"}
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "abs_challenges.json"


def dur(ev: dict) -> float:
    s = datetime.fromisoformat(ev["startTime"].replace("Z", "+00:00"))
    e = datetime.fromisoformat(ev["endTime"].replace("Z", "+00:00"))
    return (e - s).total_seconds()


def main() -> int:
    rows: list[dict] = []
    ambiguous = 0
    mj_total = 0

    with httpx.Client(headers=UA, timeout=120.0) as c:
        games: list[int] = []
        for d in DATES:
            r = c.get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={d}")
            r.raise_for_status()
            for day in r.json().get("dates", []):
                games += [g["gamePk"] for g in day["games"]
                          if g["status"]["detailedState"] == "Final"]
        print(f"games: {len(games)}", file=sys.stderr)

        for i, pk in enumerate(games, 1):
            if i % 25 == 0:
                print(f"  ...{i}/{len(games)}", file=sys.stderr)
            try:
                feed = c.get(
                    f"https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live"
                ).json()
            except Exception as exc:  # noqa: BLE001
                print(f"  skip {pk}: {exc}", file=sys.stderr)
                continue

            for play in feed["liveData"]["plays"]["allPlays"]:
                rd = play.get("reviewDetails")
                if not rd or rd.get("reviewType") != ABS_REVIEW_TYPE:
                    continue
                mj_total += 1

                called = [
                    e for e in play.get("playEvents", [])
                    if e.get("isPitch")
                    and e["details"]["call"]["code"] in CALLED_CODES
                    and e.get("pitchData", {}).get("coordinates", {}).get("y0") is not None
                ]
                if not called:
                    ambiguous += 1
                    continue

                att = attribute_challenge([(k, dur(e)) for k, e in enumerate(called)])
                if att is None:
                    ambiguous += 1
                    continue

                ev = called[att.index]
                co = ev["pitchData"]["coordinates"]
                rows.append({
                    "game_pk": pk,
                    "play_id": ev.get("playId"),
                    "at_bat_index": play["about"]["atBatIndex"],
                    "stored_call_code": ev["details"]["call"]["code"],
                    "is_overturned": bool(rd.get("isOverturned")),
                    "attribution_tier": att.tier.value,
                    "review_pause_s": round(att.review_pause_s or 0.0, 2),
                    "sz_top": ev["pitchData"]["strikeZoneTop"],
                    "sz_bot": ev["pitchData"]["strikeZoneBottom"],
                    "x0": co["x0"], "y0": co["y0"], "z0": co["z0"],
                    "vx0": co["vX0"], "vy0": co["vY0"], "vz0": co["vZ0"],
                    "ax": co["aX"], "ay": co["aY"], "az": co["aZ"],
                })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dates": DATES,
        "games": len(games),
        "mj_plays": mj_total,
        "attributed": len(rows),
        "ambiguous": ambiguous,
        "challenges": rows,
    }, indent=1), encoding="utf-8")

    print(f"\nMJ plays      : {mj_total}")
    print(f"attributed    : {len(rows)} ({100*len(rows)/max(mj_total,1):.1f}%)")
    print(f"ambiguous     : {ambiguous}")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
