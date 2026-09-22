"""MLB StatsAPI backend -- the primary source.

StatsAPI is the only place that carries `playId` (hence video) and the ABS challenge
records, so it is the backbone. Its one trap is that `pitchData.coordinates.pX/pZ` are
FRONT-of-plate, unlike Savant's middle-of-plate values; this module never reads them.
Location is always re-derived from the trajectory (PLAN.md §5.1).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..groundtruth import (
    ABS_REVIEW_TYPE,
    CALLED_CODES,
    attribute_challenge,
    reconstruct_calls,
    zone_verdict,
)
from ..schema import (
    ChallengeRecord,
    PitchRecord,
    PitchTrajectory,
    Provenance,
    StrikeZone,
    make_pitch_id,
)
from .base import CachedFetcher, payload_hash

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule?sportId=1&gameType=R&startDate={s}&endDate={e}"
FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live"

# Pitch results that represent an umpire ball/strike judgement.
# automatic_ball (pitch-clock violations) is excluded: nobody judged anything.
TAKEN_DESCRIPTIONS = frozenset({"Ball", "Called Strike", "Ball In Dirt"})


def _duration_s(ev: dict) -> float:
    start = datetime.fromisoformat(ev["startTime"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(ev["endTime"].replace("Z", "+00:00"))
    return (end - start).total_seconds()


def _has_trajectory(ev: dict) -> bool:
    c = ev.get("pitchData", {}).get("coordinates", {})
    return all(c.get(k) is not None for k in
               ("x0", "y0", "z0", "vX0", "vY0", "vZ0", "aX", "aY", "aZ"))


class StatsAPISource:
    name = "statsapi"

    def __init__(self, cache_dir: Path) -> None:
        self.fetcher = CachedFetcher(Path(cache_dir) / "statsapi")

    def close(self) -> None:
        self.fetcher.close()

    # --- schedule ---------------------------------------------------------------------

    def completed_games(self, start: str, end: str) -> list[dict]:
        """Regular-season games with status Final. The sampling frame (PLAN.md §14.4).

        Re-enumerated on every call rather than hardcoded -- the 2026 season was still in
        progress when this project began, so any fixed count would be wrong.
        """
        raw = self.fetcher.get_text(
            SCHEDULE_URL.format(s=start, e=end), f"schedule_{start}_{end}.json"
        )
        import json

        out: list[dict] = []
        for day in json.loads(raw).get("dates", []):
            for g in day["games"]:
                if g["status"]["detailedState"] == "Final":
                    out.append({
                        "game_pk": g["gamePk"],
                        "date": g["officialDate"],
                        "venue_id": g.get("venue", {}).get("id"),
                        "venue_name": g.get("venue", {}).get("name"),
                    })
        return out

    # --- per-game ---------------------------------------------------------------------

    def fetch_game(self, game_pk: int) -> list[PitchRecord]:
        raw = self.fetcher.get_text(FEED_URL.format(pk=game_pk), f"game_{game_pk}.json")
        prov = Provenance(
            source=self.name,
            fetched_at=datetime.now().astimezone(),
            source_hash=payload_hash(raw),
        )

        import json

        feed = json.loads(raw)
        game_data = feed["gameData"]
        live = feed["liveData"]

        game_date = datetime.fromisoformat(game_data["datetime"]["officialDate"]).date()
        venue = game_data.get("venue", {})

        hp_id = hp_name = None
        for off in live.get("boxscore", {}).get("officials", []):
            if off.get("officialType") == "Home Plate":
                hp_id = off["official"]["id"]
                hp_name = off["official"]["fullName"]

        records: list[PitchRecord] = []
        for play in live["plays"]["allPlays"]:
            records.extend(
                self._records_for_play(play, game_pk, game_date, venue, hp_id, hp_name, prov)
            )
        return records

    def _records_for_play(
        self, play: dict, game_pk: int, game_date, venue: dict,
        hp_id: int | None, hp_name: str | None, prov: Provenance,
    ) -> list[PitchRecord]:
        matchup = play["matchup"]
        about = play["about"]
        at_bat_number = about["atBatIndex"] + 1

        events = play.get("playEvents", [])
        pitches = [e for e in events if e.get("isPitch")]

        # Savant counts `no_pitch` events (pitch-timer violations -> automatic balls) in its
        # pitch_number; StatsAPI does not. Track the running offset so the two sources can
        # actually be joined. Without this, 0.3% of pitches pair with the wrong row.
        savant_numbers: dict[int, int] = {}
        offset = 0
        pitch_i = 0
        for e in events:
            if e.get("type") == "no_pitch" and e.get("details", {}).get("call"):
                offset += 1
            elif e.get("isPitch"):
                savant_numbers[pitch_i] = e.get("pitchNumber", pitch_i + 1) + offset
                pitch_i += 1

        # --- which pitch, if any, was challenged? ---
        challenged_idx: int | None = None
        challenge_tier = None
        review_pause = None
        rd = play.get("reviewDetails")
        if rd and rd.get("reviewType") == ABS_REVIEW_TYPE:
            called = [
                (i, e) for i, e in enumerate(pitches)
                if e["details"]["call"]["code"] in CALLED_CODES and _has_trajectory(e)
            ]
            att = attribute_challenge([(i, _duration_s(e)) for i, e in called])
            if att is not None:
                challenged_idx = att.index
                challenge_tier = att.tier
                review_pause = att.review_pause_s

        # StatsAPI reports the count AFTER each pitch. Game state handed to a model must be
        # strictly PRE-pitch, or it encodes the outcome being predicted (PLAN.md §7.4).
        # Deriving it as "the previous pitch's post-count" uses MLB's own accounting and
        # sidesteps the edge cases (a foul with two strikes does not increment).
        pre_counts: list[tuple[int, int]] = []
        prev = (0, 0)
        for ev in pitches:
            pre_counts.append(prev)
            cnt = ev.get("count", {})
            prev = (cnt.get("balls", prev[0]), cnt.get("strikes", prev[1]))

        # Outs are taken from the first pitch of the play: the pre-play out state.
        pre_outs = pitches[0].get("count", {}).get("outs", 0) if pitches else 0

        out: list[PitchRecord] = []
        for i, ev in enumerate(pitches):
            if not _has_trajectory(ev):
                continue
            pd = ev["pitchData"]
            c = pd["coordinates"]
            if pd.get("strikeZoneTop") is None or pd.get("strikeZoneBottom") is None:
                continue

            trajectory = PitchTrajectory(
                x0=c["x0"], y0=c["y0"], z0=c["z0"],
                vx0=c["vX0"], vy0=c["vY0"], vz0=c["vZ0"],
                ax=c["aX"], ay=c["aY"], az=c["aZ"],
            )
            zone = StrikeZone(top=pd["strikeZoneTop"], bottom=pd["strikeZoneBottom"])
            verdict = zone_verdict(trajectory, zone)

            details = ev["details"]
            description = details.get("description", "")
            is_taken = description in TAKEN_DESCRIPTIONS

            was_challenged = i == challenged_idx
            umpire_call, abs_call = reconstruct_calls(
                details["call"]["code"],
                was_challenged=was_challenged,
                is_overturned=bool(rd.get("isOverturned")) if was_challenged and rd else False,
            )

            challenge = None
            if was_challenged and rd is not None and challenge_tier is not None:
                player = rd.get("player") or {}
                challenge = ChallengeRecord(
                    is_overturned=bool(rd.get("isOverturned")),
                    challenger_id=player.get("id"),
                    challenger_name=player.get("fullName"),
                    challenge_team_id=rd.get("challengeTeamId"),
                    attribution_tier=challenge_tier,
                    review_pause_s=review_pause,
                )

            breaks = pd.get("breaks", {})
            pre_balls, pre_strikes = pre_counts[i]

            out.append(PitchRecord(
                pitch_id=make_pitch_id(game_pk, at_bat_number, ev.get("pitchNumber", i + 1)),
                game_pk=game_pk,
                at_bat_number=at_bat_number,
                pitch_number=ev.get("pitchNumber", i + 1),
                savant_pitch_number=savant_numbers.get(i),
                play_id=ev.get("playId"),
                game_date=game_date,
                pitcher_id=matchup["pitcher"]["id"],
                pitcher_name=matchup["pitcher"].get("fullName"),
                pitcher_hand=matchup["pitchHand"]["code"],
                batter_id=matchup["batter"]["id"],
                batter_name=matchup["batter"].get("fullName"),
                batter_stand=matchup["batSide"]["code"],
                hp_umpire_id=hp_id,
                hp_umpire_name=hp_name,
                trajectory=trajectory,
                zone=zone,
                release_speed=pd.get("startSpeed"),
                release_extension=pd.get("extension"),
                spin_rate=breaks.get("spinRate"),
                spin_axis=breaks.get("spinDirection"),
                pitch_type=details.get("type", {}).get("code"),
                pitch_name=details.get("type", {}).get("description"),
                plate_time=pd.get("plateTime"),
                balls=pre_balls,
                strikes=pre_strikes,
                outs=pre_outs,
                inning=about["inning"],
                is_top=about["isTopInning"],
                runner_on_1b=matchup.get("postOnFirst") is not None,
                runner_on_2b=matchup.get("postOnSecond") is not None,
                runner_on_3b=matchup.get("postOnThird") is not None,
                venue_id=venue.get("id"),
                venue_name=venue.get("name"),
                description=description,
                is_taken=is_taken,
                umpire_call=umpire_call if is_taken else None,
                abs_call=abs_call,
                challenge=challenge,
                ground_truth=verdict.call,
                margin_in=verdict.margin_in,
                provenance=prov,
            ))
        return out
