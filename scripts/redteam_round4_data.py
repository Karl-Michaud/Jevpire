"""Red-team round 4, data-side checks.

RT-12  Is the ABS zone stable for a given batter ACROSS games, or only within one?
       The plan derives it from measured height; if it drifts, that assumption breaks.
RT-15  What fraction of taken pitches are missing trajectory / zone fields? The pipeline
       needs a realistic loss budget, not an assumption of completeness.
RT-16  The plan clusters the bootstrap by GAME. But umpires rotate and work many games,
       so if umpire skill varies, game-clustering understates dependence. Test it.

Run:  uv run python scripts/redteam_round4_data.py
"""

from __future__ import annotations

import io
import sys
import time

import httpx
import numpy as np
import polars as pl

HALF_WIDTH, BALL_RADIUS = 0.70833, 0.12083
TAKEN = ("ball", "called_strike", "blocked_ball")
UA = {"User-Agent": "Mozilla/5.0 (jevpire research; non-commercial)"}


def savant(d1: str, d2: str) -> pl.DataFrame:
    url = ("https://baseballsavant.mlb.com/statcast_search/csv?all=true&hfGT=R%7C"
           f"&game_date_gt={d1}&game_date_lt={d2}&player_type=pitcher&type=details")
    r = httpx.get(url, headers=UA, timeout=180.0)
    r.raise_for_status()
    return pl.read_csv(io.StringIO(r.text), infer_schema_length=10000, ignore_errors=True)


def rt15_missing(raw: pl.DataFrame) -> pl.DataFrame:
    print("=== RT-15: missing-field budget on taken pitches ===")
    t = raw.filter(pl.col("description").is_in(TAKEN))
    n = len(t)
    print(f"  taken pitches (pre-filter): {n:,}")
    for col in ("plate_x", "plate_z", "sz_top", "sz_bot", "vx0", "ax", "release_pos_y",
                "release_speed", "pitch_type"):
        miss = int(t.select(pl.col(col).is_null().sum()).item())
        print(f"    {col:<16} missing {miss:>5}  ({100*miss/n:5.2f}%)")
    usable = t.drop_nulls(["plate_x", "plate_z", "sz_top", "sz_bot"])
    print(f"  usable after dropping nulls: {len(usable):,} ({100*len(usable)/n:.2f}%)\n")
    return usable


def rt12_zone_stability(d: pl.DataFrame) -> None:
    print("=== RT-12: is the ABS zone stable for a batter across games? ===")
    g = (d.group_by(["batter", "game_pk"])
           .agg(sz_top=pl.col("sz_top").first())
           .group_by("batter")
           .agg(games=pl.len(),
                distinct=pl.col("sz_top").n_unique(),
                spread_in=((pl.col("sz_top").max() - pl.col("sz_top").min()) * 12)))
    multi = g.filter(pl.col("games") >= 2)
    print(f"  batters seen in >=2 games: {len(multi)}")
    if len(multi):
        stable = int((multi["distinct"] == 1).sum())
        print(f"    identical sz_top in every game : {stable} ({100*stable/len(multi):.1f}%)")
        sp = multi["spread_in"].to_numpy()
        print(f"    spread (inches): median={np.median(sp):.3f} p90={np.percentile(sp,90):.3f} "
              f"max={sp.max():.3f}")
        worst = multi.sort("spread_in", descending=True).head(5)
        print("    largest drifts:")
        for r in worst.iter_rows(named=True):
            print(f"      batter {r['batter']}  {r['games']} games  "
                  f"{r['distinct']} distinct  spread {r['spread_in']:.3f} in")
    print()


def rt16_clustering(d: pl.DataFrame, game_umpire: dict[int, str]) -> None:
    print("=== RT-16: should the bootstrap cluster by game or by umpire? ===")
    d = d.with_columns(
        margin=pl.min_horizontal(
            (HALF_WIDTH + BALL_RADIUS) - pl.col("plate_x").abs(),
            (pl.col("sz_top") + BALL_RADIUS) - pl.col("plate_z"),
            pl.col("plate_z") - (pl.col("sz_bot") - BALL_RADIUS)))
    d = d.with_columns(
        correct=((pl.col("margin") >= 0).cast(pl.Int8)
                 == (pl.col("description") == "called_strike").cast(pl.Int8)).cast(pl.Int8),
        umpire=pl.col("game_pk").replace_strict(game_umpire, default=None))
    d = d.drop_nulls("umpire")
    print(f"  pitches with a known HP umpire: {len(d):,} "
          f"over {d['game_pk'].n_unique()} games, {d['umpire'].n_unique()} umpires")

    per_ump = (d.group_by("umpire")
                 .agg(n=pl.len(), acc=pl.col("correct").mean())
                 .filter(pl.col("n") >= 80)
                 .sort("acc"))
    a = per_ump["acc"].to_numpy()
    print(f"  umpires with >=80 pitches: {len(per_ump)}")
    print(f"    accuracy spread: min={a.min()*100:.2f}%  median={np.median(a)*100:.2f}%  "
          f"max={a.max()*100:.2f}%  sd={a.std()*100:.2f} pp")
    exp_sd = np.sqrt(0.9475 * 0.0525 / per_ump["n"].mean()) * 100
    print(f"    sd expected from sampling noise alone: {exp_sd:.2f} pp")
    print(f"    -> {'REAL between-umpire variation' if a.std()*100 > 1.3*exp_sd else 'consistent with noise'}")

    games_per_ump = d.group_by("umpire").agg(g=pl.col("game_pk").n_unique())["g"].to_numpy()
    print(f"  games per umpire in this sample: mean={games_per_ump.mean():.1f} "
          f"max={games_per_ump.max()}")

    rng = np.random.default_rng(3)
    for unit in ("game_pk", "umpire"):
        keys = d[unit].unique().to_list()
        buckets = {k: d.filter(pl.col(unit) == k)["correct"].to_numpy() for k in keys}
        accs = []
        for _ in range(500):
            vals = np.concatenate([buckets[keys[i]] for i in rng.integers(0, len(keys), len(keys))])
            accs.append(vals.mean())
        lo, hi = np.percentile(accs, [2.5, 97.5])
        print(f"  cluster by {unit:<8}: 95% CI = [{lo*100:.2f}, {hi*100:.2f}]  "
              f"width {100*(hi-lo):.2f} pp")
    print()


def main() -> int:
    windows = [("2026-06-15", "2026-06-17"), ("2026-08-10", "2026-08-12")]
    raws = []
    for d1, d2 in windows:
        print(f"fetching {d1}..{d2}", file=sys.stderr)
        raws.append(savant(d1, d2))
    raw = pl.concat(raws, how="vertical_relaxed")

    usable = rt15_missing(raw)
    rt12_zone_stability(usable)

    games = usable["game_pk"].unique().to_list()
    print(f"fetching HP umpires for {len(games)} games...", file=sys.stderr)
    ump: dict[int, str] = {}
    with httpx.Client(headers=UA, timeout=120.0) as c:
        for i, g in enumerate(games, 1):
            try:
                bs = c.get(f"https://statsapi.mlb.com/api/v1/game/{g}/boxscore").json()
                for o in bs.get("officials", []):
                    if o.get("officialType") == "Home Plate":
                        ump[g] = o["official"]["fullName"]
            except Exception:  # noqa: BLE001
                pass
            if i % 25 == 0:
                print(f"  ...{i}/{len(games)}", file=sys.stderr)
            time.sleep(0.12)
    rt16_clustering(usable, ump)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
