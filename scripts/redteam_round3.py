"""Red-team round 3: a fresh sweep over the REVISED plan.

Round 2 verified the round-1 fixes but was not a new attack. These are claims in the
revised plan that had never been tested.

RT-7  §7.2 leakage tripwire: "if condition D (context only) scores well above the base
      rate, location is leaking." Is that true, or can context legitimately predict?
RT-8  §14.3 escalation rule: "if |delta| < 2.5 pp, extend to 5,000." Does 5,000 actually
      resolve the 1-2 pp blind spot, or does the rule send us somewhere still underpowered?
RT-9  §14.4 sampling frame assumes ~2,430 completed 2026 regular-season games. Verify.

Run:  uv run python scripts/redteam_round3.py
"""

from __future__ import annotations

import io
import sys

import httpx
import numpy as np
import polars as pl
from sklearn.ensemble import HistGradientBoostingClassifier

HALF_WIDTH = 0.70833
BALL_RADIUS = 0.12083
TAKEN = ("ball", "called_strike", "blocked_ball")
UA = {"User-Agent": "Mozilla/5.0 (jevpire research; non-commercial)"}


def savant(d1: str, d2: str) -> pl.DataFrame:
    url = (
        "https://baseballsavant.mlb.com/statcast_search/csv?all=true&hfGT=R%7C"
        f"&game_date_gt={d1}&game_date_lt={d2}&player_type=pitcher&type=details"
    )
    r = httpx.get(url, headers=UA, timeout=180.0)
    r.raise_for_status()
    return pl.read_csv(io.StringIO(r.text), infer_schema_length=10000, ignore_errors=True)


def prepare(df: pl.DataFrame) -> pl.DataFrame:
    d = df.filter(
        pl.col("description").is_in(TAKEN)
        & pl.col("plate_x").is_not_null()
        & pl.col("plate_z").is_not_null()
        & pl.col("sz_top").is_not_null()
    )
    margin = pl.min_horizontal(
        (HALF_WIDTH + BALL_RADIUS) - pl.col("plate_x").abs(),
        (pl.col("sz_top") + BALL_RADIUS) - pl.col("plate_z"),
        pl.col("plate_z") - (pl.col("sz_bot") - BALL_RADIUS),
    ) * 12.0
    return d.with_columns(
        margin_in=margin,
        is_strike=(margin >= 0).cast(pl.Int8),
        ump_strike=(pl.col("description") == "called_strike").cast(pl.Int8),
    )


def rt7_context_only(d: pl.DataFrame) -> None:
    """Can context alone beat the base rate WITHOUT any location information?"""
    print("\n=== RT-7: is the condition-D leakage tripwire valid? ===")
    d = d.with_columns(
        stand_r=(pl.col("stand") == "R").cast(pl.Int8),
        throws_r=(pl.col("p_throws") == "R").cast(pl.Int8),
        pt=pl.col("pitch_type").cast(pl.Categorical).to_physical().cast(pl.Int32),
    ).drop_nulls(["release_speed", "pfx_x", "pfx_z", "pt"])

    groups = {
        "count only (balls, strikes)": ["balls", "strikes"],
        "count + handedness": ["balls", "strikes", "stand_r", "throws_r"],
        "count + hand + pitch type/velo/movement": [
            "balls", "strikes", "stand_r", "throws_r", "pt",
            "release_speed", "pfx_x", "pfx_z",
        ],
        "+ inning, outs (full condition D)": [
            "balls", "strikes", "stand_r", "throws_r", "pt",
            "release_speed", "pfx_x", "pfx_z", "inning", "outs_when_up",
        ],
    }

    games = d["game_pk"].unique().to_numpy()
    rng = np.random.default_rng(0)
    rng.shuffle(games)
    cut = int(len(games) * 0.7)
    tr = d.filter(pl.col("game_pk").is_in(games[:cut].tolist()))
    te = d.filter(pl.col("game_pk").is_in(games[cut:].tolist()))

    base = float(max((te["is_strike"] == 0).mean(), (te["is_strike"] == 1).mean()))
    print(f"  base rate (always BALL)                          {base*100:6.2f}%")
    print()
    for name, feats in groups.items():
        m = HistGradientBoostingClassifier(random_state=0)
        m.fit(tr.select(feats).to_numpy(), tr["is_strike"].to_numpy())
        acc = float((m.predict(te.select(feats).to_numpy()) == te["is_strike"].to_numpy()).mean())
        print(f"  {name:<48} {acc*100:6.2f}%   ({(acc-base)*100:+.2f} pp)")

    print("\n  Strike rate by count (why context carries real signal):")
    by = (
        d.group_by(["balls", "strikes"])
        .agg(n=pl.len(), strike_pct=(pl.col("is_strike").mean() * 100).round(1))
        .sort(["balls", "strikes"])
    )
    for row in by.iter_rows(named=True):
        if row["n"] > 200:
            print(f"    {row['balls']}-{row['strikes']}  n={row['n']:>5}  in-zone {row['strike_pct']:>5.1f}%")


def rt8_escalation_power(d: pl.DataFrame) -> None:
    """Does n=5,000 actually resolve the 1-2 pp blind spot the plan escalates into?"""
    print("\n=== RT-8: does the escalation target (n=5,000) resolve the blind spot? ===")
    margins = d["margin_in"].to_numpy()
    ump_ok = (d["is_strike"] == d["ump_strike"]).to_numpy().astype(int)
    rng = np.random.default_rng(11)

    print(f"  {'sigma(in)':<10} {'n':>7} {'Jev acc':>9} {'ump acc':>9} {'discord':>8} {'power':>7}")
    print("  " + "-" * 54)
    for sigma in (0.75, 1.0, 1.5):
        for n in (1000, 3000, 5000, 10000):
            rej = ja = ua = dc = 0
            reps = 300
            for _ in range(reps):
                idx = rng.integers(0, len(margins), n)
                m, u = margins[idx], ump_ok[idx]
                jev_ok = ((m + rng.normal(0, sigma, n) >= 0) == (m >= 0)).astype(int)
                b = int(((jev_ok == 1) & (u == 0)).sum())
                c = int(((jev_ok == 0) & (u == 1)).sum())
                ja += jev_ok.mean(); ua += u.mean(); dc += b + c
                if b + c > 0 and (abs(b - c) - 1) ** 2 / (b + c) > 3.841:
                    rej += 1
            print(f"  {sigma:<10} {n:>7} {ja/reps*100:>8.2f}% {ua/reps*100:>8.2f}% "
                  f"{dc/reps:>8.0f} {rej/reps:>7.2f}")
        print()


def rt9_sampling_frame() -> None:
    """Is the 2026 regular season actually complete as of today?"""
    print("=== RT-9: 2026 regular-season sampling frame ===")
    r = httpx.get(
        "https://statsapi.mlb.com/api/v1/schedule?sportId=1&gameType=R"
        "&startDate=2026-03-01&endDate=2026-11-30",
        headers=UA, timeout=180.0,
    )
    r.raise_for_status()
    states: dict[str, int] = {}
    first = last = None
    for day in r.json().get("dates", []):
        for g in day["games"]:
            states[g["status"]["detailedState"]] = states.get(g["status"]["detailedState"], 0) + 1
            d = g["officialDate"]
            first = d if first is None or d < first else first
            last = d if last is None or d > last else last
    total = sum(states.values())
    print(f"  regular-season games on schedule : {total}")
    print(f"  date range                       : {first} .. {last}")
    for k, v in sorted(states.items(), key=lambda kv: -kv[1]):
        print(f"    {k:<28} {v:>5}")
    final = states.get("Final", 0)
    print(f"\n  COMPLETED (Final): {final}  ({100*final/total:.1f}% of schedule)")


def main() -> int:
    frames = []
    for d1, d2 in [("2026-04-20", "2026-04-22"), ("2026-06-15", "2026-06-17"),
                   ("2026-08-10", "2026-08-12"), ("2026-09-05", "2026-09-07")]:
        print(f"fetching {d1}..{d2}", file=sys.stderr)
        frames.append(prepare(savant(d1, d2)))
    d = pl.concat(frames, how="vertical_relaxed")
    print(f"\npool: {len(d):,} taken pitches over {d['game_pk'].n_unique()} games")

    rt9_sampling_frame()
    rt7_context_only(d)
    rt8_escalation_power(d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
