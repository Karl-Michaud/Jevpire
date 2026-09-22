"""Flattening PitchRecord to and from Parquet.

Parquet on local disk is the working copy of record. Supabase is a publishing target that
can always be repopulated from it (PLAN.md §13.2) -- which matters because free-tier
projects pause after a week of inactivity.

The flattening is explicit rather than reflective so that a schema change is a visible diff.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import polars as pl

from .schema import (
    SCHEMA_VERSION,
    AttributionTier,
    Call,
    ChallengeRecord,
    PitchRecord,
    PitchTrajectory,
    Provenance,
    StrikeZone,
    VideoRef,
)


def record_to_row(r: PitchRecord) -> dict:
    t, z, c, v = r.trajectory, r.zone, r.challenge, r.video
    x_mid, z_mid = r.plate_location
    return {
        "pitch_id": r.pitch_id,
        "game_pk": r.game_pk,
        "at_bat_number": r.at_bat_number,
        "pitch_number": r.pitch_number,
        "savant_pitch_number": r.savant_pitch_number,
        "play_id": r.play_id,
        "game_date": r.game_date,
        "pitcher_id": r.pitcher_id,
        "pitcher_name": r.pitcher_name,
        "pitcher_hand": r.pitcher_hand,
        "batter_id": r.batter_id,
        "batter_name": r.batter_name,
        "batter_stand": r.batter_stand,
        "catcher_id": r.catcher_id,
        "hp_umpire_id": r.hp_umpire_id,
        "hp_umpire_name": r.hp_umpire_name,
        # trajectory, anchored at y=50
        "x0": t.x0, "y0": t.y0, "z0": t.z0,
        "vx0": t.vx0, "vy0": t.vy0, "vz0": t.vz0,
        "ax": t.ax, "ay": t.ay, "az": t.az,
        "sz_top": z.top, "sz_bot": z.bottom,
        # derived convenience columns -- NOT authoritative; always recomputed from the fit
        "plate_x_mid": x_mid, "plate_z_mid": z_mid,
        "release_speed": r.release_speed,
        "release_extension": r.release_extension,
        "spin_rate": r.spin_rate,
        "spin_axis": r.spin_axis,
        "pitch_type": r.pitch_type,
        "pitch_name": r.pitch_name,
        "plate_time": r.plate_time,
        "balls": r.balls, "strikes": r.strikes, "outs": r.outs,
        "inning": r.inning, "is_top": r.is_top,
        "runner_on_1b": r.runner_on_1b,
        "runner_on_2b": r.runner_on_2b,
        "runner_on_3b": r.runner_on_3b,
        "score_bat": r.score_bat, "score_fld": r.score_fld,
        "venue_id": r.venue_id, "venue_name": r.venue_name,
        "description": r.description,
        "is_taken": r.is_taken,
        "umpire_call": r.umpire_call.value if r.umpire_call else None,
        "abs_call": r.abs_call.value if r.abs_call else None,
        "ground_truth": r.ground_truth.value,
        "margin_in": r.margin_in,
        "challenge_overturned": c.is_overturned if c else None,
        "challenger_id": c.challenger_id if c else None,
        "challenger_name": c.challenger_name if c else None,
        "challenge_team_id": c.challenge_team_id if c else None,
        "challenge_tier": c.attribution_tier.value if c else None,
        "review_pause_s": c.review_pause_s if c else None,
        "video_path": v.local_path if v else None,
        "video_bytes": v.bytes if v else None,
        "video_width": v.width if v else None,
        "video_height": v.height if v else None,
        "video_fps": v.fps if v else None,
        "video_duration_s": v.duration_s if v else None,
        "source": r.provenance.source,
        "fetched_at": r.provenance.fetched_at,
        "schema_version": r.provenance.schema_version,
        "source_hash": r.provenance.source_hash,
    }


def row_to_record(d: dict) -> PitchRecord:
    challenge = None
    if d.get("challenge_tier"):
        challenge = ChallengeRecord(
            is_overturned=bool(d["challenge_overturned"]),
            challenger_id=d.get("challenger_id"),
            challenger_name=d.get("challenger_name"),
            challenge_team_id=d.get("challenge_team_id"),
            attribution_tier=AttributionTier(d["challenge_tier"]),
            review_pause_s=d.get("review_pause_s"),
        )
    video = None
    if d.get("video_path"):
        video = VideoRef(
            play_id=d["play_id"],
            local_path=d["video_path"],
            bytes=d.get("video_bytes"),
            width=d.get("video_width"),
            height=d.get("video_height"),
            fps=d.get("video_fps"),
            duration_s=d.get("video_duration_s"),
        )
    fetched = d["fetched_at"]
    if isinstance(fetched, str):
        fetched = datetime.fromisoformat(fetched)

    return PitchRecord(
        pitch_id=d["pitch_id"], game_pk=d["game_pk"],
        at_bat_number=d["at_bat_number"], pitch_number=d["pitch_number"],
        savant_pitch_number=d.get("savant_pitch_number"),
        play_id=d.get("play_id"), game_date=d["game_date"],
        pitcher_id=d["pitcher_id"], pitcher_name=d.get("pitcher_name"),
        pitcher_hand=d["pitcher_hand"],
        batter_id=d["batter_id"], batter_name=d.get("batter_name"),
        batter_stand=d["batter_stand"], catcher_id=d.get("catcher_id"),
        hp_umpire_id=d.get("hp_umpire_id"), hp_umpire_name=d.get("hp_umpire_name"),
        trajectory=PitchTrajectory(
            x0=d["x0"], y0=d["y0"], z0=d["z0"],
            vx0=d["vx0"], vy0=d["vy0"], vz0=d["vz0"],
            ax=d["ax"], ay=d["ay"], az=d["az"],
        ),
        zone=StrikeZone(top=d["sz_top"], bottom=d["sz_bot"]),
        release_speed=d.get("release_speed"),
        release_extension=d.get("release_extension"),
        spin_rate=d.get("spin_rate"), spin_axis=d.get("spin_axis"),
        pitch_type=d.get("pitch_type"), pitch_name=d.get("pitch_name"),
        plate_time=d.get("plate_time"),
        balls=d["balls"], strikes=d["strikes"], outs=d["outs"],
        inning=d["inning"], is_top=d["is_top"],
        runner_on_1b=d.get("runner_on_1b", False),
        runner_on_2b=d.get("runner_on_2b", False),
        runner_on_3b=d.get("runner_on_3b", False),
        score_bat=d.get("score_bat"), score_fld=d.get("score_fld"),
        venue_id=d.get("venue_id"), venue_name=d.get("venue_name"),
        description=d["description"], is_taken=d["is_taken"],
        umpire_call=Call(d["umpire_call"]) if d.get("umpire_call") else None,
        abs_call=Call(d["abs_call"]) if d.get("abs_call") else None,
        challenge=challenge,
        ground_truth=Call(d["ground_truth"]), margin_in=d["margin_in"],
        video=video,
        provenance=Provenance(
            source=d["source"], fetched_at=fetched,
            schema_version=d.get("schema_version", SCHEMA_VERSION),
            source_hash=d.get("source_hash"),
        ),
    )


def to_dataframe(records: list[PitchRecord]) -> pl.DataFrame:
    return pl.DataFrame([record_to_row(r) for r in records], infer_schema_length=None)


def write_parquet(records: list[PitchRecord], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    to_dataframe(records).write_parquet(path, compression="zstd")
    return path


def read_parquet(path: Path) -> list[PitchRecord]:
    return [row_to_record(d) for d in pl.read_parquet(path).to_dicts()]
