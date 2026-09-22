# Task 01 — Full 2026 season acquisition

**Branch:** `feat/full-season-acquisition`
**Depends on:** Phase 0 + Phase 1 (merged to `main`)
**Blocks:** Part 1 evaluation, the escalated sample (PLAN.md §14.3), the strengthened
ground-truth fixture

---

## Why the full season, and why only 2026

The 2026 season is the **only** season this project can use. Two silent redefinitions landed
with the ABS rollout (PLAN.md §3.1): `plate_x`/`plate_z` moved from the front of the plate to
the middle, and `sz_top`/`sz_bot` became the height-derived ABS zone rather than an
operator's per-PA estimate. Pre-2026 data is a different measurement on a different zone and
would need its own validation. Mixing seasons would be the single easiest way to produce a
confidently wrong result.

One season is ample: **~682k pitches, ~360k of them judged.** The v1 evaluation set is 1,000
and the pre-registered escalation is 10,000, so the season over-covers the largest design by
36×.

What the full season unlocks that a 100-game sample cannot:

| | 10 games (now) | Full season |
|---|---|---|
| ABS challenges for rule validation | 10 | **~2,900** |
| Games per home-plate umpire | 1 | **~30** |
| Sampling frame for the evaluation set | — | the real one |
| Population parameters | estimated from 4 windows | exact |

The ~2,900 challenges matter most: the ground-truth fixture currently rests on 164 rulings,
and this raises it by roughly 18×. The ~30 games per umpire settle RT-16 — at v1 scale game
and umpire were interchangeable clustering units only because each umpire appeared ~1.3
times; at season scale they genuinely diverge, and between-umpire variation becomes
measurable rather than indistinguishable from noise.

---

## Scope — measured, not guessed

Extrapolated from the 10-game Milestone 1 run (291.4 pitches/game, 0.41 MB parquet/10 games,
0.77 MB raw JSON/game):

| Artefact | Size | Time |
|---|---|---|
| Pitches | ~682k (~360k taken) | — |
| ABS challenges | ~2,900 | — |
| **Processed Parquet** | **~96 MB** | — |
| Raw JSON cache | ~1.75 GB | ~62 min |
| Savant CSV cache (~186 dates) | ~1 GB | ~10 min |
| **Video, whole season** | **~2,900 GB** | see below |
| Video, taken pitches only | ~1,550 GB | ~half the above |
| Video, 10k-pitch subset | ~42 GB | ~4 h |

S: has 3,722 GB free.

> **Decision: structured data AND video for the entire season.**
>
> Whole-season video fits — 2,900 GB of 3,722 GB free, leaving ~820 GB of headroom. The
> binding constraint is wall-clock, not disk.

**Time.** The serial downloader manages ~40 clips/min (two requests per clip: resolve the
page, then fetch the signed mp4), which is ~12 days for 682k clips. That is the number to
fix, and it is fixable — the job is network-bound, not CPU-bound, so modest concurrency
helps almost linearly until bandwidth saturates:

| Concurrency | Clips/min | Wall-clock | Sustained throughput needed |
|---|---|---|---|
| 1 (today) | ~40 | ~12 days | 2.9 MB/s |
| 4 | ~160 | ~3 days | 11 MB/s |
| 8 | ~320 | ~36 h | 23 MB/s |

Past ~8 workers this becomes bandwidth-bound rather than request-bound, so the realistic
range is **1.5–6 days depending on the connection**. Build it resumable and let it run
across sessions; do not try to finish it in one sitting.

`--taken-only` halves everything (~1,550 GB, ~18 h at concurrency 8) and still covers every
ball/strike experiment, since Parts 2 and 3 only ever look at pitches the batter took. The
extra half exists solely for the deferred richer-outcome extension
(`SWINGING_STRIKE` / `FOUL` / `IN_PLAY`). Default to the full pull as decided; keep the flag
so the cheap subset is one argument away.

---

## What to build

### 1. Season acquisition, resumable

`jevpire acquire-season --start 2026-03-25 --end 2026-09-27`

The current `acquire` holds every record in memory and writes one Parquet at the end. At
2,342 games that is several GB of pydantic objects and a single point of failure an hour
into the run. Required changes:

- **Shard by game.** Write `data/processed/season/game_<pk>.parquet` per game, or append to
  month-level shards. Never hold the season in memory.
- **Manifest.** `data/processed/season/_manifest.json` recording, per game: status, pitch
  count, gate outcome, payload hash, timestamp. A re-run skips completed games.
- **Resume by default.** `--force` to re-fetch. The raw JSON cache already makes re-derivation
  free; the manifest makes re-*fetching* free too.
- **Progress + ETA**, because this is a ~1 hour job.
- **Failures are data.** A game that fails three times is recorded in the manifest with the
  reason and skipped, not fatal. Report the count at the end.

### 2. Gates at season scale

`run_gates` currently takes `list[PitchRecord]`. At 682k rows that is the wrong shape.

- Add a Polars path that reads the shards lazily and computes the same gates over columns.
- Keep the record-based path — the tests use it, and it is the readable definition.
- **The two paths must agree.** Add a test asserting identical gate output on the 10-game
  sample.
- Savant cross-check runs per date against the cached CSVs.

### 3. Sampling, seeded and recorded

`jevpire sample --n 1000 --games 100 --seed <s>`

Implements PLAN.md §14.4:

1. Frame = completed regular-season games from the manifest.
2. Stratify on month, **proportional to completed games per month** — March is 4 days and
   September is partial, so equal weighting would distort. Record which scheme was used.
3. Within each game, simple random sample without replacement from taken pitches.
4. Do **not** balance classes or over-sample close calls. Natural distribution is the
   deployment condition; stratified *reporting* recovers the close-call view.
5. Emit the realised distribution over month, venue, pitcher hand, batter stand, count and
   pitch type. **Report it; do not tune it.**

Three disjoint outputs:

| Set | Size | Purpose |
|---|---|---|
| `eval_1000.parquet` | 1,000 pitches / 100 games | the v1 evaluation set |
| `dev_200.parquet` | 200 pitches / 20 **separate** games | prompt development only, never evaluated |
| `challenges.parquet` | all ~2,900 | rule validation + hard-cases head-to-head |

Disjointness by `game_pk` is a test, not a convention.

### 4. Regenerate the ground-truth fixture at scale

`scripts/build_challenge_fixture.py` currently samples 10 fixed dates → 164 rulings. Point it
at the season manifest to capture ~2,900.

- Keep the fixture **committed** — it is the CI gate and must not require network.
- ~2,900 rows of compact JSON is a few MB. Acceptable. If it exceeds ~10 MB, store a seeded
  subsample of 1,000 plus the aggregate agreement figure.
- The existing tests should pass unchanged at 18× the evidence. **If agreement drops below
  100 %, that is a finding — investigate before adjusting anything.**

### 5. Season-scale video download

The existing `video` command is correct but serial, un-resumable across runs, and has no
disk guard. At 682k clips those are all blocking defects.

- **Concurrency.** Async or a thread pool, `--concurrency` defaulting to 6. Two requests per
  clip (page → signed mp4); the signed URLs expire, so never cache the link, only the file.
- **Resume across sessions.** A manifest (`data/video/_manifest.json`) recording per
  `play_id`: status, bytes, ffprobe metadata, attempt count, last error. Files already on
  disk are skipped without a network call. The job must survive being killed at hour 30.
- **Disk guard.** Stop cleanly with a clear message when free space on S: drops below
  **100 GB**. Filling the drive mid-run is worse than stopping short.
- **Ordering matters.** Fetch in priority order so the dataset is useful before the job
  finishes: (1) the challenge set, (2) `eval_1000`, (3) `dev_200`, (4) all remaining taken
  pitches, (5) everything else. If it is interrupted on day 3, everything v1 and the
  deferred parts need is already present.
- **Rate limiting stays.** Per-worker throttle so total request rate stays civil. Back off on
  429/5xx rather than hammering; three strikes then record and move on.
- **Progress + ETA + running GB**, logged to a file, since nobody watches a 36-hour job.
- `--taken-only` to pull the ~1,550 GB half.

Expect a non-trivial failure count at this scale. Failures are recorded in the manifest and
reported, never fatal.

---

## Definition of done

- [ ] All ~2,342 completed games acquired; manifest complete; failures enumerated with reasons
- [ ] Season gates pass; Polars and record paths produce identical output on the 10-game sample
- [ ] Population figures land in the red-team ranges: taken 50–54 %, BALL 66–72 %, umpire
      94–96 %, close calls (<1 in) ~10–12 %
- [ ] ~2,900 challenges attributed at ≥95 %; rule-vs-ABS agreement reported **per tier**
- [ ] Fixture regenerated; `pytest` green
- [ ] `eval_1000`, `dev_200`, `challenges` written, disjoint by `game_pk`, seeds recorded
- [ ] Realised sampling distribution reported in `docs/LOG.md`
- [ ] Video downloader is concurrent, resumable across sessions, disk-guarded and
      priority-ordered; manifest records every `play_id`
- [ ] Video pulled for `challenges` + `eval_1000` + `dev_200` (the priority head) — these
      gate the task; the full-season remainder runs on afterwards and does not block
- [ ] `docs/LOG.md` entry with the exact population parameters

## Explicitly not in this task

Any Jev call. Any prompt construction. Any CV or video analysis. Any metric beyond the gates.
Any season other than 2026.

---

## Watch for

**The season is still in progress.** 2,458 games are scheduled through 2026-09-27; 2,342 were
Final as of 2026-09-22, with 87 Scheduled and 27 Postponed. Always filter on
`status.detailedState == "Final"` and re-enumerate rather than hardcoding a count — and
re-running later will legitimately pick up more games, so the manifest must record *when* the
frame was enumerated.

**The two sources number pitches differently.** StatsAPI omits `no_pitch` events (pitch-timer
violations) from `pitch_number`; Savant counts them. `PitchRecord.savant_pitch_number` exists
for this. Joining on the wrong one pairs the wrong rows and produced a 4.15 ft discrepancy
before it was fixed.

**Zone corruption is real but rare.** ~0.07 % of pitches carry a strike zone inconsistent with
their batter's constant. Quarantined, never corrected.

**Be polite to MLB.** ~2,500 feed requests plus ~186 CSV pulls in one run. The 0.15 s throttle
in `CachedFetcher` stays. Cache aggressively; a re-run must never re-fetch.
