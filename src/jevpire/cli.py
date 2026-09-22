"""Jevpire CLI. Acquisition and verification are commands, not notebooks, so every run is
reproducible from its arguments plus the recorded seed.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Annotated

import polars as pl
import typer

from .quality import run_gates, validate_record
from .schema import Call
from .sources.savant import SavantSource
from .sources.statsapi import StatsAPISource
from .sources.video import VideoSource
from .storage import read_parquet, write_parquet

app = typer.Typer(add_completion=False, help="Jevpire acquisition and verification.")

DATA = Path("S:/Jevpire/data")
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
VIDEO = DATA / "video"
QUARANTINE = DATA / "quarantine"


@app.command()
def acquire(
    start: Annotated[str, typer.Option(help="Season start, YYYY-MM-DD")] = "2026-03-25",
    end: Annotated[str, typer.Option(help="Season end, YYYY-MM-DD")] = "2026-09-27",
    games: Annotated[int, typer.Option(help="Number of games to sample")] = 10,
    seed: Annotated[int, typer.Option(help="Sampling seed; recorded in the output name")] = 42,
    out: Annotated[str, typer.Option(help="Output parquet name")] = "",
    cross_check: Annotated[bool, typer.Option(help="Fetch Savant for gate 1")] = True,
) -> None:
    """Sample completed games, build PitchRecords, run the quality gates, write Parquet."""
    src = StatsAPISource(RAW)
    typer.echo(f"enumerating completed regular-season games {start} .. {end}")
    frame = src.completed_games(start, end)
    typer.echo(f"  sampling frame: {len(frame)} games with status Final")

    rng = random.Random(seed)
    chosen = rng.sample(frame, min(games, len(frame)))
    chosen.sort(key=lambda g: (g["date"], g["game_pk"]))
    typer.echo(f"  sampled {len(chosen)} games (seed {seed})")

    records = []
    for i, g in enumerate(chosen, 1):
        typer.echo(f"  [{i}/{len(chosen)}] {g['date']} game {g['game_pk']} {g['venue_name']}")
        try:
            records.extend(src.fetch_game(g["game_pk"]))
        except Exception as exc:  # noqa: BLE001
            typer.secho(f"      FAILED: {exc}", fg=typer.colors.RED)
    src.close()

    if not records:
        typer.secho("no records acquired", fg=typer.colors.RED)
        raise typer.Exit(1)

    # per-row sanity -> quarantine
    clean, bad = [], []
    for r in records:
        reason = validate_record(r)
        (bad if reason else clean).append((r, reason) if reason else r)
    if bad:
        QUARANTINE.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(
            [{"pitch_id": r.pitch_id, "reason": why} for r, why in bad]
        ).write_csv(QUARANTINE / f"quarantine_seed{seed}.csv")
        typer.secho(f"  quarantined {len(bad)} rows", fg=typer.colors.YELLOW)

    savant_index = None
    if cross_check:
        dates = sorted({g["date"] for g in chosen})
        typer.echo(f"  cross-checking against Savant ({dates[0]} .. {dates[-1]})")
        sav = SavantSource(RAW)
        savant_index = {}
        for d in dates:
            try:
                savant_index.update(sav.index_by_key(d, d))
            except Exception as exc:  # noqa: BLE001
                typer.secho(f"      Savant {d} failed: {exc}", fg=typer.colors.YELLOW)
        sav.close()
        typer.echo(f"  Savant rows indexed: {len(savant_index)}")

    typer.echo("\n--- quality gates ---")
    report = run_gates(clean, savant_index=savant_index)
    typer.echo(report.render())

    # Drop rows the gates flagged (corrupt zone -> unreliable ground-truth label) so the
    # published dataset contains only rows we stand behind.
    flagged = {pid for pid, _ in report.quarantined}
    if flagged:
        QUARANTINE.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(
            [{"pitch_id": pid, "reason": why} for pid, why in report.quarantined]
        ).write_csv(QUARANTINE / f"gate_quarantine_seed{seed}.csv")
        clean = [r for r in clean if r.pitch_id not in flagged]
        typer.secho(f"  dropped {len(flagged)} gate-flagged rows", fg=typer.colors.YELLOW)

    name = out or f"pitches_{len(chosen)}games_seed{seed}.parquet"
    path = write_parquet(clean, PROCESSED / name)
    typer.echo(f"\nwrote {len(clean)} records -> {path}")

    if not report.passed:
        typer.secho("QUALITY GATES FAILED", fg=typer.colors.RED, bold=True)
        raise typer.Exit(2)


@app.command()
def video(
    parquet: Annotated[str, typer.Argument(help="Processed parquet to pull clips for")],
    limit: Annotated[int, typer.Option(help="Max clips (0 = all)")] = 0,
    taken_only: Annotated[bool, typer.Option(help="Only taken pitches")] = False,
) -> None:
    """Download pitch video. Collected now even though v1 does not use it (PLAN.md §1.1)."""
    records = read_parquet(PROCESSED / parquet if not Path(parquet).exists() else parquet)
    targets = [r for r in records if r.play_id]
    if taken_only:
        targets = [r for r in targets if r.is_taken]
    if limit:
        targets = targets[:limit]

    typer.echo(f"{len(targets)} clips to fetch")
    vs = VideoSource(RAW, VIDEO)
    counts: dict[str, int] = {}
    total_bytes = 0
    for i, r in enumerate(targets, 1):
        res = vs.fetch(r.play_id, probe=(i <= 20))
        key = res.status.split(":")[0]
        counts[key] = counts.get(key, 0) + 1
        if res.ref and res.ref.bytes:
            total_bytes += res.ref.bytes
        if i % 25 == 0 or i == len(targets):
            typer.echo(f"  [{i}/{len(targets)}] {counts}")
    vs.close()

    typer.echo(f"\n{counts}")
    typer.echo(f"total: {total_bytes/1e9:.2f} GB")
    ok = counts.get("downloaded", 0) + counts.get("cached", 0)
    rate = ok / max(len(targets), 1)
    typer.echo(f"coverage: {rate:.1%}")
    if rate < 0.95:
        typer.secho("coverage below the 95% gate", fg=typer.colors.RED)
        raise typer.Exit(2)


@app.command()
def verify(
    parquet: Annotated[str, typer.Argument(help="Processed parquet to check")],
) -> None:
    """Re-run the quality gates on an existing dataset, plus a lossless round-trip check."""
    path = PROCESSED / parquet if not Path(parquet).exists() else Path(parquet)
    records = read_parquet(path)
    typer.echo(f"{len(records)} records from {path}")

    report = run_gates(records)
    typer.echo(report.render())

    tmp = path.with_suffix(".roundtrip.parquet")
    write_parquet(records, tmp)
    again = read_parquet(tmp)
    tmp.unlink(missing_ok=True)
    lossless = len(again) == len(records) and all(
        a.pitch_id == b.pitch_id
        and a.ground_truth == b.ground_truth
        and abs(a.margin_in - b.margin_in) < 1e-9
        for a, b in zip(records, again)
    )
    typer.echo(f"[{'PASS' if lossless else 'FAIL'}] round-trip Parquet -> PitchRecord")
    if not (report.passed and lossless):
        raise typer.Exit(2)


@app.command()
def summary(
    parquet: Annotated[str, typer.Argument(help="Processed parquet to summarise")],
) -> None:
    """Coverage report: the numbers Phase 1 is judged on (PLAN.md §17)."""
    path = PROCESSED / parquet if not Path(parquet).exists() else Path(parquet)
    records = read_parquet(path)
    taken = [r for r in records if r.is_taken]
    ch = [r for r in records if r.challenge]

    typer.echo(f"pitches            : {len(records):,}")
    typer.echo(f"games              : {len({r.game_pk for r in records})}")
    typer.echo(f"taken              : {len(taken):,} ({100*len(taken)/len(records):.1f}%)")
    if taken:
        ball = sum(1 for r in taken if r.ground_truth is Call.BALL)
        agree = sum(1 for r in taken if r.ground_truth == r.umpire_call)
        close = sum(1 for r in taken if r.is_close_call)
        typer.echo(f"class balance      : {100*ball/len(taken):.1f}% BALL")
        typer.echo(f"umpire vs rule     : {100*agree/len(taken):.2f}%")
        typer.echo(f"close calls (<1in) : {close} ({100*close/len(taken):.1f}%)")
    typer.echo(f"ABS challenges     : {len(ch)}")
    if ch:
        ov = sum(1 for r in ch if r.challenge.is_overturned)
        ok = sum(1 for r in ch if r.ground_truth == r.abs_call)
        typer.echo(f"  overturn rate    : {100*ov/len(ch):.1f}%")
        typer.echo(f"  rule vs ABS      : {ok}/{len(ch)}")
    typer.echo(f"with play_id       : {sum(1 for r in records if r.play_id):,}")
    typer.echo(f"with video         : {sum(1 for r in records if r.video):,}")
    typer.echo(f"distinct pitchers  : {len({r.pitcher_id for r in records})}")
    typer.echo(f"distinct batters   : {len({r.batter_id for r in records})}")
    typer.echo(f"distinct umpires   : {len({r.hp_umpire_id for r in records if r.hp_umpire_id})}")
    typer.echo(f"venues             : {len({r.venue_id for r in records if r.venue_id})}")


if __name__ == "__main__":
    app()
