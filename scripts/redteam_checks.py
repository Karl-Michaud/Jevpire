"""Red-team checks backing docs/PLAN.md.

Each function answers one challenge raised against the plan. Results are printed and
should be pasted into docs/PLAN_REDTEAM.md with the date they were produced.

Run:  uv run python scripts/redteam_checks.py
"""

from __future__ import annotations

import io
import sys
from dataclasses import dataclass

import httpx
import numpy as np
import polars as pl
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline

HALF_WIDTH = 0.70833   # 8.5 in, half of home plate
BALL_RADIUS = 0.12083  # 1.45 in
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
    """Filter to taken pitches and attach geometric ground truth + margin."""
    d = df.filter(
        pl.col("description").is_in(TAKEN)
        & pl.col("plate_x").is_not_null()
        & pl.col("sz_top").is_not_null()
        & pl.col("plate_z").is_not_null()
    )
    slack_x = (HALF_WIDTH + BALL_RADIUS) - pl.col("plate_x").abs()
    slack_t = (pl.col("sz_top") + BALL_RADIUS) - pl.col("plate_z")
    slack_b = pl.col("plate_z") - (pl.col("sz_bot") - BALL_RADIUS)
    margin = pl.min_horizontal(slack_x, slack_t, slack_b) * 12.0
    return d.with_columns(
        margin_in=margin,
        is_strike=(margin >= 0).cast(pl.Int8),
        ump_strike=(pl.col("description") == "called_strike").cast(pl.Int8),
    )


@dataclass
class Split:
    Xtr: np.ndarray
    ytr: np.ndarray
    Xte: np.ndarray
    yte: np.ndarray


def group_split(d: pl.DataFrame, feats: list[str], target: str, seed: int = 0) -> Split:
    """Split by game_pk, never by pitch -- pitches in a game are not independent."""
    games = d["game_pk"].unique().to_numpy()
    rng = np.random.default_rng(seed)
    rng.shuffle(games)
    cut = int(len(games) * 0.7)
    tr_games, te_games = set(games[:cut].tolist()), set(games[cut:].tolist())
    tr = d.filter(pl.col("game_pk").is_in(list(tr_games)))
    te = d.filter(pl.col("game_pk").is_in(list(te_games)))
    return Split(
        tr.select(feats).to_numpy(), tr[target].to_numpy(),
        te.select(feats).to_numpy(), te[target].to_numpy(),
    )


def check_baselines(d: pl.DataFrame) -> None:
    """R11: the plan assumed logistic regression on raw coords hits ~99%. Verify."""
    print("\n=== R11: baseline model accuracy (group-split by game, 70/30) ===")
    print("Target A = geometric rule.  Target B = the umpire's actual call.\n")

    d = d.with_columns(
        abs_plate_x=pl.col("plate_x").abs(),
        dist_top=pl.col("sz_top") - pl.col("plate_z"),
        dist_bot=pl.col("plate_z") - pl.col("sz_bot"),
    )

    raw = ["plate_x", "plate_z", "sz_top", "sz_bot"]
    eng = ["abs_plate_x", "dist_top", "dist_bot"]

    print(f"{'model':<44} {'features':<10} {'target':<9} {'test acc':>9}")
    print("-" * 76)
    for target, tname in (("is_strike", "A rule"), ("ump_strike", "B human")):
        for fname, feats in (("raw", raw), ("engineered", eng)):
            s = group_split(d, feats, target)
            for mname, model in (
                ("LogisticRegression", make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000))),
                ("HistGradientBoosting", HistGradientBoostingClassifier(random_state=0)),
            ):
                model.fit(s.Xtr, s.ytr)
                acc = float((model.predict(s.Xte) == s.yte).mean())
                print(f"{mname:<44} {fname:<10} {tname:<9} {acc*100:>8.2f}%")
        # reference points
        s = group_split(d, raw, target)
        maj = float(max((s.yte == 0).mean(), (s.yte == 1).mean()))
        print(f"{'-- majority class':<44} {'':<10} {tname:<9} {maj*100:>8.2f}%")
        if target == "ump_strike":
            te = d.filter(pl.col("game_pk").is_in(
                d["game_pk"].unique().to_numpy()[
                    int(len(d["game_pk"].unique()) * 0.7):
                ].tolist()
            ))
            agree = float((te["is_strike"] == te["ump_strike"]).mean())
            print(f"{'-- geometric rule as predictor of human':<44} {'':<10} {tname:<9} {agree*100:>8.2f}%")
        print()


def check_separability(d: pl.DataFrame) -> None:
    """R11 supporting evidence: why raw features fail for a linear model."""
    print("=== R11b: linear signal in raw vs engineered features ===")
    y = d["is_strike"].to_numpy().astype(float)
    for name, v in (
        ("plate_x        (raw)", d["plate_x"].to_numpy()),
        ("|plate_x|      (eng)", d["plate_x"].abs().to_numpy()),
        ("plate_z        (raw)", d["plate_z"].to_numpy()),
        ("sz_top-plate_z (eng)", (d["sz_top"] - d["plate_z"]).to_numpy()),
    ):
        print(f"  corr({name}, is_strike) = {np.corrcoef(v, y)[0, 1]:+.4f}")
    print("\n  The strike region is |x| <= c -- symmetric in plate_x. A linear model")
    print("  cannot represent an absolute value, so raw plate_x carries almost no")
    print("  linear signal. This is a feature-engineering result, not a model-capacity one.\n")


def check_blocked_ball(d: pl.DataFrame) -> None:
    """R4: does blocked_ball inflate accuracy with trivial cases?"""
    print("=== R4: contribution of each taken-pitch description ===")
    g = (
        d.group_by("description")
        .agg(
            n=pl.len(),
            pct_geometric_strike=(pl.col("is_strike").mean() * 100).round(1),
            mean_abs_margin_in=(pl.col("margin_in").abs().mean()).round(1),
        )
        .sort("n", descending=True)
    )
    print(g)
    with_bb = float((d["is_strike"] == d["ump_strike"]).mean())
    nb = d.filter(pl.col("description") != "blocked_ball")
    without = float((nb["is_strike"] == nb["ump_strike"]).mean())
    print(f"\n  umpire accuracy including blocked_ball: {with_bb*100:.2f}%")
    print(f"  umpire accuracy excluding blocked_ball: {without*100:.2f}%")
    print(f"  sensitivity: {abs(with_bb-without)*100:.2f} pp\n")


def main() -> int:
    windows = [
        ("2026-04-20", "2026-04-22"),
        ("2026-06-15", "2026-06-17"),
        ("2026-08-10", "2026-08-12"),
        ("2026-09-05", "2026-09-07"),
    ]
    frames = []
    for d1, d2 in windows:
        print(f"fetching {d1} .. {d2}", file=sys.stderr)
        frames.append(prepare(savant(d1, d2)))
    d = pl.concat(frames, how="vertical_relaxed")
    print(f"\npool: {len(d):,} taken pitches over {d['game_pk'].n_unique()} games\n")

    check_blocked_ball(d)
    check_separability(d)
    check_baselines(d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
