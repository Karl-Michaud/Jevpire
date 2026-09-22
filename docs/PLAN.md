# Jevpire — Project Plan (SSOT)

**Status:** draft v1, 2026-09-22
**Authority:** This document is the single source of truth. Every other document, experiment
and module in this repo derives from it. If code and this document disagree, one of them is
a bug — decide which, fix it, and record the decision in `docs/LOG.md`.

**Companion documents**
- `docs/DATA_DICTIONARY.md` — field-level reference for 2026 MLB pitch data. Verified empirically.
- `docs/LOG.md` — append-only research log. Nothing changes methodology without an entry.

---

## 0. How to read this document

Sections 1–3 are the *why*. Sections 4–8 are the *what*. Sections 9–13 are the *how we know
we didn't fool ourselves*. Sections 14–19 are the *how we build it*.

Statistical concepts are explained where first used, because this is a first data-science
project and the reasoning matters more than the vocabulary.

Every quantitative claim below marked **[measured]** was computed from live MLB APIs on
2026-09-22; the measurement is reproducible from the code this plan specifies. Claims marked
**[assumed]** are not yet verified and are tracked in §19.

---

## 1. Project overview

Jev is a "System One" decision model from TypeSafe AI, released 2026-09-15. It is not an LLM.
It accepts a block of state (text / JSON) and a set of typed questions, and returns typed
values with **calibrated probabilities and confidence** — no generated text. Its headline
claims are (a) frontier-level accuracy on classification-shaped tasks, (b) 70–500 ms
end-to-end latency, and (c) calibration, via a training method they call RLCD (Reinforcement
Learning for Calibrated Decisions).

Calling balls and strikes is an unusually good test bed for those three claims simultaneously:

- It is a **binary decision under a published, objective rule** — so correctness is not a
  matter of taste.
- There is a **strong human baseline** with a known error rate.
- There is an **independent machine adjudicator** (ABS) on a subset of pitches.
- It is **latency-critical in a way that is physically meaningful**: a pitch is in flight for
  390–450 ms, which is the same order as Jev's quoted response time.

The project is deliberately scoped as a small, honest research study — not a product, not a
demonstration that Jev wins.

### 1.1 Scope of this version

**In scope now:** Part 0 (dataset), Part 1 (structured data → Jev), Part 2 (video → CV →
Jev).

**Deferred:** Part 3 (pre-emptive prediction) and richer outcome classes. The dataset built in
Part 0 must nonetheless capture everything Part 3 will need — video, trajectory parameters,
frame-rate-resolved timing — because re-acquiring data later is expensive and introduces
version skew.

### 1.2 The non-negotiable principle

We are not trying to make Jev win. `Human > ABS > Jev` is a publishable result.
`Jev > Human` is a publishable result. "Jev is bad at this and here is precisely why" is the
*most* useful result, because it maps the boundary of a new model class.

Concretely this means: metrics and decision rules are fixed **before** results are seen
(§13.6), and every post-hoc analysis is labelled exploratory.

---

## 2. Research questions

Primary, in priority order:

- **RQ1 — Accuracy.** How accurately does Jev classify taken pitches as balls or strikes,
  given structured pitch data, relative to (a) the geometric ABS zone rule, (b) the human
  umpire, and (c) trivial and simple-model baselines?
- **RQ2 — Calibration.** Are Jev's reported probabilities and confidence honest? When it says
  80%, is it right 80% of the time? This is a direct test of TypeSafe's central claim.
- **RQ3 — Latency.** What is Jev's latency distribution in practice (not just the mean), and
  how does it decompose into model time vs. pipeline time?
- **RQ4 — Information sufficiency.** How does accuracy degrade as we withhold information —
  from exact coordinates, to relative geometry, to context only? This tells us whether Jev is
  *reasoning about location* or *pattern-matching on priors*.
- **RQ5 — Vision.** Can a computer-vision model recover enough pitch geometry from broadcast
  video to support the same decision, and how much accuracy is lost versus Statcast-derived
  geometry?

Deferred:

- **RQ6 — Earliness.** How early in the flight can a correct decision be made? (Part 3.)

### 2.1 Two distinct targets, never conflated

There are two different things "correct" can mean, and confusing them is the most likely way
this project produces a meaningless number.

| Target | Label | Nature | What it tests |
|---|---|---|---|
| **A — Rule** | Geometric ABS zone call (§6) | Deterministic | Can Jev apply a precise spatial rule? |
| **B — Human** | What the umpire actually called | Stochastic | Can Jev model human judgment, including framing and bias? |

These are reported as separate columns in every results table, always. Agreement with ABS is
a third, separate quantity, measurable only on the ~0.3% of pitches that were challenged (§6.3).

---

## 3. Findings that shape the design

These were established before writing this plan and each one changed it.

### 3.1 The 2026 data redefinitions **[measured]**

From 2026, `plate_x`/`plate_z` are measured at the **middle** of the plate (y = 0.70833 ft),
not the front, and `sz_top`/`sz_bot` **are** the ABS zone (0.535 × height, 0.270 × height).
StatsAPI still reports front-of-plate coordinates while Savant reports middle-of-plate. They
disagree by up to ~1.2 inches vertically — larger than the margin on most contested pitches.

**Consequence:** no module may read `plate_x`/`pX` directly. Location is always re-derived
from trajectory parameters at an explicitly named plane (§5.1).

### 3.2 Ground truth is solvable and validated **[measured]**

The ABS rule — strike iff any part of the ball touches a 17-inch-wide rectangle spanning
`sz_bot` to `sz_top` at the middle of the plate — reproduced **164 of 164** official ABS
rulings across 135 games spanning April to September 2026, at an attribution rate of 98.2 %
(§6.5). Crucially, the two independent attribution tiers each reached 100 % separately
(81/81 and 83/83), which is what rules out a selection artefact — see `PLAN_REDTEAM.md` RT-1.

Competing specifications fail clearly, which is what makes this a test rather than a fit:

| Plane | Ball radius | Agreement |
|---|---|---|
| **middle** | **1.45 in** | **100.0 %** |
| middle | 0.72 in | 81.0 % |
| middle | 0 | 60.8 % |
| front | 1.45 in | 94.9 % |
| front | 0.72 in | 86.1 % |
| front | 0 | 72.2 % |

### 3.3 ABS coverage is sparse **[measured]**

ABS adjudicates only challenged pitches: 79 across 90 games, 44 across 110 games — roughly
**0.3 % of pitches, ~0.4–0.9 per game**, with a 36–44 % overturn rate.

**Consequence:** "Jev vs. ABS" is not an experiment on a random sample. It is a small,
biased, hard-cases evaluation. The challenge set's primary role is **validating** the
geometric rule (§3.2); its secondary role is a head-to-head on the hardest pitches.

### 3.4 The task is easy on average and brutal at the edges **[measured]**

From 24,619 taken pitches across 160 games, sampled in four windows spanning the season
(April, June, August, September). The numbers are stable across all four — umpire accuracy
ranges 94.20–95.18 %, taken fraction 51.7–53.4 %, BALL class 68.7–70.1 % — so they are
properties of the season, not of one hot week. Detail in `PLAN_REDTEAM.md` RT-3.

Headline figures (June window shown for the difficulty breakdown; pooled where noted):

| Quantity | Value |
|---|---|
| Pitches that are actually judged (taken) | 5,796 = **51.9 %** |
| Class balance (geometric truth) | 68.7 % BALL / 31.3 % STRIKE |
| **Human umpire accuracy vs. the rule** | **94.2 %** |
| False strikes (ball called strike) | 201 |
| False balls (strike called ball) | 135 |

Accuracy as a function of distance from the nearest zone edge:

| Within | n | % of taken | Umpire accuracy |
|---|---|---|---|
| 0.5 in | 304 | 5.2 % | **60.5 %** |
| 1 in | 637 | 11.0 % | 66.1 % |
| 2 in | 1,271 | 21.9 % | 76.2 % |
| 3 in | 1,873 | 32.3 % | 82.7 % |
| 6 in | 3,419 | 59.0 % | 90.3 % |
| 12 in | 5,013 | 86.5 % | 93.3 % |

**Consequence:** headline accuracy on a random sample is dominated by easy pitches and will
compress every method into the 90s. The *interesting* analysis is stratified by margin. A
"close-call" slice (|margin| < 1 in) is a required reporting stratum, not an optional extra.

### 3.5 Statcast trajectories are retrospective **[measured]**

The nine trajectory parameters are a least-squares fit to the *completed* flight. The pitch
event window in the API is ~3.9 s (median) against a ball flight of 0.39–0.47 s.

**Consequence for Part 3 (deferred, but decided now):** truncating a Statcast trajectory to
simulate early prediction leaks information from the part of the flight being withheld. That
experiment is a legitimate *upper bound* but cannot support an earliness claim. The honest
version requires video frames, which are causally clean (frame *t* contains only what was
visible at *t*). This is why Part 0 must collect video now.

### 3.6 Video is available and its frame rate bounds Part 3 **[measured]**

Savant clips: 1280×720, **59.57 fps**, ~7.5 s, ~4.3 MB, available for 20/20 sampled pitches
from a regular-season game. One frame = 16.8 ms; a whole pitch flight is only ~24 frames.

### 3.7 Jev's documented weaknesses are directly relevant

TypeSafe's own documentation states Jev "cannot count or perform arithmetic reliably" and
"does not extract values from free text." Deciding whether `|x| ≤ 0.829` is arithmetic.

**Consequence:** this is the single largest threat to Part 1 being a meaningful experiment,
and it is addressed head-on by the input-condition ablation in §7.2 rather than ignored.

---

## 4. Part 0 — dataset architecture

### 4.1 Design goal

One canonical, versioned, reproducible pitch-level dataset that serves **every** experiment,
including deferred ones. Acquisition happens once. Experiments read; they never fetch.

### 4.2 The source-agnostic interface

This is the structural spine of the project and the thing most worth getting right.

```
          ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
          │  StatsAPI    │   │ Savant CSV   │   │  CV pipeline │
          │  backend     │   │ backend      │   │  (Part 2)    │
          └──────┬───────┘   └──────┬───────┘   └──────┬───────┘
                 │                  │                  │
                 └────────┬─────────┴──────────────────┘
                          ▼
                   ┌─────────────┐
                   │ PitchRecord │   ← the only thing downstream code ever sees
                   └──────┬──────┘
                          │
         ┌────────────────┼────────────────┬──────────────────┐
         ▼                ▼                ▼                  ▼
   ground truth      Jev prompting     baselines         evaluation
```

Every backend implements one protocol:

```python
class PitchSource(Protocol):
    name: str
    def fetch_game(self, game_pk: int) -> list[PitchRecord]: ...
```

Downstream code depends only on `PitchRecord`. This is what makes the Part 2 swap
(Statcast geometry → CV-derived geometry) a one-line configuration change rather than a
rewrite, and it is what makes the Part 1 vs Part 2 comparison a controlled one: the *only*
thing that differs is where the trajectory came from.

### 4.3 The schema

```python
class Provenance:
    source: str                  # "statsapi" | "savant" | "cv:v1"
    fetched_at: datetime
    schema_version: str
    source_hash: str             # hash of the raw payload, for reproducibility

class PitchTrajectory:
    # constant-acceleration fit, ANCHORED AT y = 50 ft (see DATA_DICTIONARY §2)
    x0: float; y0: float; z0: float
    vx0: float; vy0: float; vz0: float
    ax: float;  ay: float;  az: float

    def time_to(self, y: float) -> float
    def location_at_plane(self, y: float) -> tuple[float, float]   # (x, z)
    def location_at_time(self, t: float) -> tuple[float, float, float]
    def speed_at_time(self, t: float) -> float

class StrikeZone:
    top: float                   # ft above ground
    bottom: float
    HALF_WIDTH: ClassVar = 0.70833      # 8.5 in
    BALL_RADIUS: ClassVar = 0.12083     # 1.45 in
    ABS_PLANE_Y: ClassVar = 0.70833     # middle of plate

class PitchRecord:
    # --- identity ---
    pitch_id: str                # f"{game_pk}-{at_bat_number}-{pitch_number}"
    game_pk: int
    at_bat_number: int
    pitch_number: int
    play_id: str | None          # StatsAPI UUID -> video. None if unavailable.
    game_date: date

    # --- participants ---
    pitcher_id: int; pitcher_name: str; pitcher_hand: Literal["L","R"]
    batter_id: int;  batter_name: str;  batter_stand: Literal["L","R"]
    catcher_id: int | None
    hp_umpire_id: int | None; hp_umpire_name: str | None

    # --- physics ---
    trajectory: PitchTrajectory
    zone: StrikeZone
    release_speed: float; release_extension: float
    spin_rate: float | None; spin_axis: float | None
    pitch_type: str; pitch_name: str
    plate_time: float            # release -> plate, seconds

    # --- game state (all pre-pitch) ---
    balls: int; strikes: int; outs: int
    inning: int; is_top: bool
    runners_on: tuple[bool, bool, bool]
    score_bat: int; score_fld: int
    venue_id: int; venue_name: str

    # --- outcome ---
    description: str             # raw Savant/StatsAPI vocabulary
    is_taken: bool               # in {ball, called_strike, blocked_ball}
    umpire_call: Call | None     # reconstructed pre-challenge (§6.4)
    abs_call: Call | None        # only if challenged
    challenge: ChallengeRecord | None

    # --- labels ---
    ground_truth: Call           # geometric rule, §6
    margin_in: float             # signed inches to nearest zone edge; >0 = strike

    # --- media (Part 2/3) ---
    video: VideoRef | None

    provenance: Provenance
```

Derived fields (`ground_truth`, `margin_in`, `is_taken`) are computed by pure functions and
never read from a source. Raw payloads are archived verbatim so any derivation can be redone
without re-fetching.

### 4.4 Storage tiers

| Tier | Contents | Location | In git? |
|---|---|---|---|
| Raw payloads | StatsAPI JSON, Savant CSV, as fetched | `S:\Jevpire\data\raw\` | **No** |
| Video | MP4 per `play_id` | `S:\Jevpire\data\video\` | **No** |
| Processed | Parquet of `PitchRecord` | `S:\Jevpire\data\processed\` | **No** |
| Dataset of record | Postgres tables | Supabase | n/a |
| Sample | ~50 rows, no video | `data/sample/` | **Yes** |
| Results | metrics JSON, figures | `results/` | **Yes** |
| Code, docs, configs | | repo | **Yes** |

No Git LFS. Video is redistributable-restricted (§18) and large; it stays local. The repo
must be clonable and the *analysis* reproducible from Supabase plus the acquisition code.

Storage estimate **[measured]**: 4.3 MB/clip → 1,000 pitches ≈ 4.3 GB, 10,000 ≈ 43 GB. The
S: drive has 3.7 TB free, so video is not a constraint at planned scale.

### 4.5 What Part 0 must collect that Part 1 does not need

Collected now specifically so Parts 2–3 are not blocked later:

- `play_id` for every pitch, and the MP4 itself
- full 9-parameter trajectory (not just `plate_x`/`plate_z`)
- `plate_time`, `release_extension`, venue
- the complete challenge record, including `reviewType` and challenger
- home-plate umpire identity
- raw payload archives

---

## 5. Data sources

### 5.1 Summary

| Source | Access | Gives us | Limitation |
|---|---|---|---|
| **MLB StatsAPI** `statsapi.mlb.com/api/v1.1/game/{pk}/feed/live` | Public JSON, no key | `play_id`, trajectory (anchored at y=50), zone, challenges, umpires, timestamps | Location is **front-of-plate**; ~800 KB/game |
| **Baseball Savant** `/statcast_search/csv` | Public CSV | 119 columns, middle-of-plate location, movement, expected stats | **No `play_id`**; `umpire`/`sv_id` empty in 2026 |
| **Savant video** `/sporty-videos?playId=` | HTML → MP4 | 720p60 clip per pitch | Requires `Referer` header; signed URLs expire |
| **Savant ABS leaderboard** `/leaderboard/abs-challenges` | Public | Aggregated challenge metrics | Aggregate only; per-pitch data comes from StatsAPI |

### 5.2 Why we need both tabular sources

StatsAPI is the only source of `play_id` (hence video) and challenge records. Savant is the
canonical middle-of-plate location. They join on `(game_pk, at_bat_number, pitch_number)`.

**Decision:** StatsAPI is the primary backend. Savant is used as a cross-check on location
and to supply fields StatsAPI lacks. Any row where the two disagree beyond tolerance is
quarantined rather than silently resolved (§12.4).

### 5.3 Acquisition budget **[measured]**

~800 KB and ~1–2 s per game feed. 200 games ≈ 160 MB, a few minutes. Video adds 4.3 MB per
pitch. All of it is cached on disk; a re-run must never re-fetch.

---

## 6. Ground truth

### 6.1 The rule

A pitch is a **STRIKE** iff, evaluating the trajectory at y = 0.70833 ft:

```
|x| ≤ 0.70833 + 0.12083
z  ≤ sz_top  + 0.12083
z  ≥ sz_bot  − 0.12083
```

and **BALL** otherwise. `margin_in` = 12 × min(those three slacks); positive means strike,
and its magnitude is the distance to the nearest edge in inches.

### 6.2 Why this is not circular

The obvious objection is that we are defining truth using the same system ABS uses, then
claiming to evaluate ABS. Three things separate this from circularity:

1. The rule is derived from **published MLB regulations** (17-inch plate, 53.5%/27% of
   height, ball touching the zone), not fitted to outcomes.
2. It is **validated out-of-sample** against 123 official ABS rulings, and only one
   specification out of six passes (§3.2). A fitted rule would not have that property.
3. Ground truth is used to evaluate **Jev and the umpire**, not ABS. ABS is a separate
   comparator reported separately.

### 6.3 What it genuinely does not establish

`ground_truth` is the ABS rule applied to **Hawk-Eye's measurement** of the pitch. If
Hawk-Eye is wrong, we are wrong in the same direction, and the validation in §3.2 cannot
detect that because ABS uses the same measurement.

MLB reports Hawk-Eye median error ≈ 0.16 in, 95% within 0.39 in. Since 5.2 % of taken
pitches sit within 0.5 in of an edge **[measured]**, measurement error plausibly flips the
label on **roughly 1–3 % of taken pitches**, concentrated entirely in the close-call
stratum.

This is stated as a limitation in the write-up, and it is the reason the close-call stratum
is reported with explicit caveats rather than treated as a clean accuracy number.

### 6.4 Reconstructing the umpire's original call

MLB rewrites `details.call.code` after an overturn, so the stored call on a challenged pitch
is already ABS-corrected.

```python
abs_call    = stored_call if challenged else None
umpire_call = flip(stored_call) if (challenged and is_overturned) else stored_call
```

Getting this backwards yields ~58 % agreement with the geometric rule — a coin flip — which
is the bug's own alarm. A regression test asserts >99 % agreement on the challenge set.

### 6.5 Identifying the challenged pitch — tiered attribution

`reviewDetails` is attached to the at-bat, not the pitch, so the challenged pitch must be
inferred. Filter `reviewType == "MJ"` first, to exclude manager and boundary reviews.

A single heuristic is not enough. Using only the review pause attributes **48.5 %** of
challenges **[measured]**, which would make the §3.2 validation a selected subsample. The
tiered rule:

```
tier 1   exactly one called pitch with endTime-startTime > 15 s   -> that pitch
tier 2   no called pitch carries a pause                          -> last called pitch of the play
drop     more than one paused pitch                               -> ambiguous, exclude
```

**[measured]** over 135 games:

| Tier | n | % of MJ plays | Agreement with official ruling |
|---|---|---|---|
| tier 1 — unique review pause | 81 | 48.5 % | 81/81 = 100 % |
| tier 2 — last called pitch | 83 | 49.7 % | 83/83 = 100 % |
| drop — multiple challenges, one record | 3 | 1.8 % | — |
| **attributed** | **164** | **98.2 %** | **164/164 = 100 %** |

The drop case is a genuine API limitation: more than one challenge can occur in a single
at-bat, but only **one** `reviewDetails` object is stored, so the record is irrecoverably
ambiguous. Those plays are excluded rather than guessed at.

The attribution tier is stored on every challenge record, so any analysis can be repeated on
tier 1 alone if the tier-2 fallback is ever doubted.

---

## 7. Part 1 — structured data → Jev

### 7.1 The threat this design exists to counter

If we hand Jev `plate_x = -0.47`, `sz_bot = 1.54`, `sz_top = 3.06` and ask for a call, we are
asking it to evaluate three inequalities. That is a test of arithmetic, which TypeSafe
explicitly documents as a weakness. A single number from that setup — high or low — would be
close to meaningless as a statement about pitch-calling.

The fix is to make **information content the independent variable**.

### 7.2 Input conditions (the core ablation)

Each condition is a different `state` block handed to Jev for the same pitch.

| ID | Condition | State contains | Tests |
|---|---|---|---|
| **A** | Raw geometry | `plate_x`, `plate_z`, `sz_top`, `sz_bot` in feet | Can Jev do the geometry at all? |
| **B** | Relative geometry | Horizontal distance from plate centre; vertical position expressed relative to zone top/bottom | Does restating the arithmetic help? |
| **C** | Solved geometry | Signed distance to nearest edge, in inches | Sanity ceiling — a sign check. Should be ~100 %. |
| **D** | Context only | Pitch type, velo, movement, count, handedness, pitcher/catcher identity. **No location.** | Floor — what do priors alone buy? Expect ≈ base rate. |
| **E** | Umpire's view | Everything in A plus everything in D | The realistic setting |

Reading the results: **C ≈ 100 %** confirms the harness works. **D ≈ 69 %** confirms no
leakage (if D scores well above the base rate, location is leaking through some other
field — a bug). The interesting quantity is **A vs. C**: the gap is exactly Jev's arithmetic
deficit, measured rather than assumed.

Condition **E** is the headline number and is the only one compared against the human umpire.

### 7.3 Prompt/state protocol

- State is a **JSON object**, not prose — this matches Jev's documented strength ("emphasis
  on structured program state").
- Field order and naming are fixed per condition and versioned. Prompt text lives in
  `src/jevpire/jev/prompts.py` with a `PROMPT_VERSION` constant recorded in every result row.
- Units are stated explicitly in the state (e.g. `"plate_x_ft"`), because Jev reads
  instructions literally.
- The question is a single `Choice` with options `BALL` and `STRIKE`.
- **Fixed before any results are seen.** Any prompt change starts a new `PROMPT_VERSION`,
  gets a `docs/LOG.md` entry, and re-runs every condition.

### 7.4 Leakage controls

Hard-excluded from every state block:

- Anything post-pitch: `description`, `events`, `type`, `zone`, `delta_run_exp`,
  `launch_speed`, `launch_angle`, `bat_speed`, all expected-outcome estimates
- `des` free text (contains the outcome in English)
- The resulting count, or the next pitch's count
- ABS/challenge fields

A unit test asserts the serialized state for each condition contains none of a denylist of
field names, and that condition D's state has zero mutual information with location by
construction (it simply cannot contain the coordinate fields).

### 7.5 Baselines — mandatory, reported alongside Jev

Without these, an accuracy number is uninterpretable.

All values below are **[measured]** on 24,619 taken pitches across 160 games, group-split by
game 70/30 (`scripts/redteam_checks.py`).

| Baseline | Target A (rule) | Target B (human call) |
|---|---|---|
| Majority class (always BALL) | 69.28 % | 68.49 % |
| Logistic regression, raw coordinates | **69.28 %** | 68.49 % |
| Logistic regression, engineered features | 71.47 % | 71.64 % |
| **HistGradientBoosting, raw coordinates** | **99.69 %** | **94.94 %** |
| HistGradientBoosting, engineered features | 99.95 % | 94.98 % |
| Geometric rule (oracle) | 100 % by construction | 94.84 % |
| Human umpire | 94.75 % | 100 % by construction |

Two things here matter, and the first one corrected an error in the original plan.

**Linear models fail completely.** Logistic regression on raw coordinates scores *exactly*
the majority-class rate — it learns nothing. The strike region is `|x| ≤ c AND z ≤ top AND
z ≥ bot`: an axis-aligned **box**, i.e. an intersection of half-spaces. A single hyperplane
cannot represent either the absolute value or the conjunction. Engineering `|plate_x|` and
the two zone distances fixes the absolute value but not the conjunction, which is why it only
reaches 71 %. Tree ensembles split on axis-aligned thresholds — precisely the right inductive
bias for a box — and reach 99.95 %. This contrast is a clean teaching example of inductive
bias and belongs in the write-up.

**The bar for Jev on Target A is therefore ~99.7 %, not ~99 %.** If a stock
`HistGradientBoostingClassifier` beats Jev on Target A, that is the honest headline and the
project's contribution becomes calibration and latency rather than accuracy. Accepted in
advance.

**Target B has a near-irreducible floor.** A tuned GBM beats "just apply the rulebook" by
**0.1 pp** at predicting the human call. Umpire deviations are close to unpredictable from
location alone. Treat ~95 % as the practical ceiling; anything above ~95.5 % should be
investigated hard for leakage before it is believed.

### 7.6 Recorded per call

Prediction; `probabilities` for both options; `confidence`; `model` version string;
model latency; end-to-end latency; state token count; `PROMPT_VERSION`; condition ID;
`pitch_id`; retry count.

---

## 8. Part 2 — video → CV → Jev

### 8.1 Why the architecture is forced

Jev accepts text and JSON only. TypeSafe's launch material explicitly notes "structured state
as a data structure with text, not on images (yet…)". So there is no "feed frames to Jev"
option, and the honest architecture is the only available one:

```
   MP4 clip (720p, 59.57 fps)
        │
        ▼
   frame extraction
        │
        ▼
   ball detection + tracking  ────────► pixel-space track
        │
        ▼
   camera calibration / geometry
        │
        ▼
   estimated (x, z) at the ABS plane  ──► PitchTrajectory (provenance = "cv:v1")
        │
        ▼
   PitchRecord  ────────────────────────► identical Jev prompt as Part 1, condition E
        │
        ▼
   BALL / STRIKE
```

The CV stage emits a `PitchTrajectory` — the same type Statcast backends emit. Everything
downstream is byte-identical to Part 1. That is what makes it a controlled comparison: the
sole manipulated variable is the provenance of the geometry.

### 8.2 The leakage risk, and how it is contained

This is the part that can silently become Part 1 in disguise.

**Absolute rule:** the CV pipeline receives the video file and nothing else. It does not
receive `plate_x`, `plate_z`, `sz_top`, `sz_bot`, the call, or any Statcast field. Enforced
by a module boundary: `src/jevpire/video/` may import `schema` but must not import any
source backend. A test asserts this.

Two subtler risks:

- **Broadcast overlays.** If clips carry a K-zone graphic, a count/score bug, or a visible
  umpire signal, the vision model can read the answer instead of seeing the ball.
  **Status: unverified.** A manual audit of 20 clips is a gate on Phase 4 (§16), recorded as
  a table in `docs/LOG.md`. If overlays leak, mitigation is to crop or to truncate the clip
  before the call is signalled.
- **Calibration fitted on Statcast.** Mapping pixels to field coordinates needs a camera
  model. Fitting that per-pitch against Statcast coordinates would import the answer.
  **Rule:** calibration is fitted on a *disjoint* calibration set of games and frozen before
  the evaluation set is touched. The evaluation set never contributes to calibration.

### 8.3 Scope discipline

Part 2's success criterion is **not** matching Statcast. A CV pipeline that recovers location
to ±2 inches is a real result, and §3.4 tells us exactly what that costs: accuracy in the
close-call stratum collapses, headline accuracy barely moves. Reporting that trade-off
*is* the finding.

Part 2 is explicitly permitted to run on a smaller sample than Part 1 (§14.3).

---

## 9. Part 3 — deferred, but constrained now

Not built in this version. Two decisions are locked in so Part 0 does not have to be redone:

- **Part 3a (Statcast truncation)** is a *simulation with known leakage* — the trajectory
  parameters were fitted using the portion of flight being withheld. It may be reported as an
  upper bound, always labelled as such. It may never support an earliness claim.
- **Part 3b (video truncation)** is the real experiment: frames up to time *t* only. Causally
  clean. Requires video, which is why Part 0 collects it now.

Frame rate bounds the resolution: 16.8 ms/frame, ~24 frames per flight **[measured]**.

Richer outcome classes (`BALL / CALLED_STRIKE / SWINGING_STRIKE / FOUL / IN_PLAY`) are not
implemented, but `description` is stored raw so the mapping can be added without re-acquisition.

---

## 10. Evaluation metrics

### 10.1 Classification

Reported for every (condition × target) cell. With STRIKE as the positive class:

- **Accuracy** — fraction correct. Interpretable only against the 68.7 % base rate.
- **Precision** — of pitches called STRIKE, the fraction that truly were. Low precision means
  the model invents strikes.
- **Recall** — of true strikes, the fraction caught. Low recall means it misses strikes.
- **F1** — harmonic mean of the two. Used because the classes are imbalanced (69/31), where
  accuracy alone flatters a model that leans toward BALL.
- **Confusion matrix**, plus the two rates that matter to the sport: **false-strike rate**
  (balls called strikes) and **false-ball rate** (strikes called balls).

### 10.2 Stratified reporting — mandatory

Every headline metric is repeated for margin bands: `<0.5″`, `0.5–1″`, `1–2″`, `2–3″`,
`3–6″`, `>6″`. Per §3.4 the aggregate is dominated by easy pitches; the bands are where the
methods actually differ.

### 10.3 Agreement

Pairwise on the same pitches: Human↔Rule, Jev↔Rule, Jev↔Human, and on the challenge subset
Jev↔ABS, Human↔ABS. Reported as raw agreement **and** Cohen's κ, which corrects for the
agreement expected by chance given a 69/31 split — raw agreement looks impressive by default
here.

> **Interpretation note — "umpire accuracy" is not umpire competence.** Umpires are trained
> to the **rulebook** zone: three-dimensional, bounded by the batter's shoulders/belt midpoint
> and the hollow of the knee, judged from the batter's stance. They are being scored here
> against the **ABS** zone: a flat plane at the middle of the plate, derived purely from
> measured height, ignoring stance entirely. The 94.75 % figure is *agreement with the ABS
> zone*, and the write-up must say so. A systematic gap between the two zone definitions
> would show up as umpire "error" that is nothing of the kind.

### 10.4 Calibration (RQ2 — the most interesting section)

- **Reliability diagram.** Bin predictions by stated probability (10 bins); plot mean
  predicted vs. observed frequency. Perfect calibration is the diagonal.
- **Expected Calibration Error (ECE).** Weighted mean gap between the two. One number for
  "how dishonest are the probabilities."
- **Brier score.** Mean squared error of the probability. Decomposed into reliability,
  resolution and uncertainty, which separates "well-calibrated" from "actually informative."
- **Confidence vs. correctness.** Jev returns `confidence` separately from `probabilities`.
  Does it rise on easy pitches and fall near the edges? Correlating `confidence` against
  `|margin_in|` is a direct test of whether Jev knows when it is guessing — the cleanest
  available probe of the RLCD claim.

Because ground truth here is near-deterministic, a *correctly* calibrated model should be
confident far from the edges and near-50/50 within ~0.5 in. Systematic overconfidence in the
close-call band is the most likely failure mode, and the most interesting if found.

### 10.5 Latency (RQ3)

Three separate clocks, never summed into one number:

- **Model latency** — the Jev API call alone.
- **Pipeline latency** — state construction and parsing.
- **End-to-end latency** — everything from `PitchRecord` in hand to decision out.

Reported as mean, median, **P90, P95, P99**, and max. Tail latency is what matters for a
real-time claim; the mean hides it. Recorded alongside: batch size, concurrency, retry count,
and wall-clock time of day. Network conditions are a confounder and are logged, not
controlled.

Jev's quoted 70–500 ms is compared against the measured 390–450 ms flight time.

---

## 11. Statistical methodology

Explained rather than asserted, because choosing the right test here is most of the work.

### 11.1 Is this supervised learning? Mostly no.

Jev is not trained by us. For Jev, this is **evaluation of a fixed system**, not model
fitting, so there is no train/test split to make and no risk of overfitting Jev to the data.

The exceptions are real and need splits:

- The **baselines** in §7.5 (logistic regression, GBM) *are* fitted. They get a proper
  group-aware split (§11.3).
- The **CV calibration** in Part 2 is fitted, and gets a disjoint calibration set.
- **Prompt selection.** If we try several prompts and pick the best, we are fitting to the
  data through the back door. Mitigation: prompts are developed on a **dev set of 200 pitches
  drawn from separate games**, frozen, and only then run on the evaluation set.

### 11.2 Class imbalance

68.7 / 31.3. Consequences: accuracy has a 68.7 % floor that is not skill; F1 and the
confusion matrix are required; and any fitted baseline must not be allowed to win by
predicting BALL. Stratified sampling maintains the natural ratio rather than balancing it,
because the natural ratio is the deployment condition.

### 11.3 Dependence — the biggest threat to the confidence intervals

Pitches are **not independent**. Pitches in a game share an umpire, a venue, a catcher and
weather. Pitches from one pitcher share movement profiles. Standard formulas assume
independence and will report intervals that are too narrow.

Two mitigations:

- **Clustered bootstrap.** Resample **whole games** with replacement, then pitches within
  them, and recompute the metric 2,000 times. The 2.5th and 97.5th percentiles give the 95 %
  interval. This propagates the real dependence structure instead of assuming it away.
- **Group-aware splits** for any fitted baseline: split by `game_pk`, never by pitch, so no
  game appears in both train and test.

The cost of ignoring this is measurable **[measured]** — see §14.1.

### 11.4 Comparing Jev to the umpire — use a paired test

Jev and the umpire judge the **same** pitches, so they are paired samples. A two-proportion
z-test would throw that away and lose power.

**McNemar's test** is the right tool: it looks only at *discordant* pairs — pitches where one
was right and the other wrong — and asks whether the split is meaningfully off 50/50. Pitches
both got right or both got wrong carry no information about which is better.

### 11.5 Multiple comparisons

With 5 conditions × 2 targets × 6 margin bands × several metrics, some "significant" result
will appear by chance at α = 0.05.

- **Primary family (pre-registered):** Jev-condition-E vs. umpire on Target A; Jev
  condition-E vs. logistic baseline; ECE of Jev condition E. Three tests,
  **Holm–Bonferroni** corrected.
- Everything else is **exploratory**, labelled as such, reported with intervals rather than
  p-values.

### 11.6 Pre-registration

Before the first evaluation run, `docs/LOG.md` records: primary metrics, the primary test
family, the sample-size rule (§14.2), and the exclusion rules. Deviations are allowed but
must be logged with a timestamp and rationale, and the original plan stays visible.

---

## 12. Leakage and confounders

### 12.1 Ranked by severity for v1

| # | Risk | Severity | Mitigation | Phase |
|---|---|---|---|---|
| 1 | Post-pitch fields in Jev state | **Critical** | Denylist + unit test (§7.4) | 1 |
| 2 | Front vs. middle of plate mixed across sources | **Critical** | Never read `plate_x`; re-derive (§5.1) | 0 |
| 3 | Umpire call not un-corrected on challenges | **Critical** | §6.4 + regression test | 0 |
| 4 | Broadcast overlay leaks the call into CV | **Critical** (Part 2) | Manual audit gate (§8.2) | 4 |
| 5 | CV calibration fitted on evaluation games | **Critical** (Part 2) | Disjoint calibration set | 4 |
| 6 | Dependence ignored → CIs too narrow | High | Clustered bootstrap (§11.3) | 1 |
| 7 | Prompt tuned on evaluation data | High | Frozen dev set (§11.1) | 1 |
| 8 | Sample concentrated in few games | High | ≥100 games (§14.1) | 0 |
| 9 | Hawk-Eye measurement error in ground truth | Medium | Documented limitation (§6.3) | — |
| 10 | Catcher framing correlates with umpire error | Medium | Recorded; exploratory analysis | 2 |
| 11 | Batter-specific zones (height) | Low | Handled: zone is per-batter | 0 |
| 12 | Missing video / missing ABS | Low | Nullable; coverage reported | 0 |

### 12.2 Deferred to later versions

Camera angle and broadcast-feed variation (Part 2 only), stadium effects, seasonal drift in
umpire behaviour, pitch-type-specific tracking error, timestamp uncertainty (Part 3 only).

### 12.3 Exclusions — fixed in advance

Excluded from all ball/strike analysis: `automatic_ball` (pitch-clock violations — no
judgment occurred), `hit_by_pitch`, all swung-at pitches, pitches with missing trajectory or
zone data, and position-player pitching (challenges are unavailable, so the ABS subset is
not comparable).

`blocked_ball` **is included**, with the sensitivity stated. It is a genuine taken pitch that
the umpire judged, but it is an easy one: 951 such pitches **[measured]**, **0 %** of them
geometric strikes, averaging **14.9 inches** outside the zone. Including them raises measured
umpire accuracy from 94.54 % to 94.75 % — a **0.21 pp** effect.

They are kept because dropping a category *after* observing that it is easy is exactly the
post-hoc choice this project is designed to avoid. The sensitivity is reported so no reader
has to wonder.

### 12.4 Data-quality gates

Acquisition fails loudly rather than silently producing a subtly wrong dataset:

1. Reconstructed location from trajectory must match Savant's published `plate_x`/`plate_z`
   to < 0.02 ft.
2. `sz_top / sz_bot` must equal 1.9815 ± 0.001 (the 53.5/27 ratio) and be constant per
   batter per game.
3. Geometric rule vs. `abs_call` must agree at > 99 % **within each attribution tier
   separately** (§6.5), and the attribution rate must exceed 95 %. Checking tiers separately
   is what lets this gate detect a broken fallback rather than a merely diluted average.
4. Taken-pitch fraction must be 50–54 %.
5. Class balance must be 66–72 % BALL.
6. Any row failing a gate goes to `data/quarantine/` with the failing check named.

These are not decorative — gates 1 and 3 are exactly the checks that caught the two real bugs
described in §3.1 and §6.4.

---

## 13. Repository architecture

```
Jevpire/
├── README.md
├── LICENSE
├── pyproject.toml
├── .env.example                 # TYPESAFE_API_KEY, SUPABASE_URL, SUPABASE_KEY
├── .gitignore                   # data/, *.mp4, .env
│
├── docs/
│   ├── PLAN.md                  # this file — SSOT
│   ├── DATA_DICTIONARY.md
│   ├── DATA_SOURCES.md
│   ├── METHODOLOGY.md
│   └── LOG.md                   # append-only research log
│
├── src/jevpire/
│   ├── schema.py                # PitchRecord, PitchTrajectory, StrikeZone, Call
│   ├── sources/
│   │   ├── base.py              # PitchSource protocol
│   │   ├── statsapi.py
│   │   ├── savant.py
│   │   └── video.py             # play_id -> mp4
│   ├── groundtruth.py           # the rule, margin, challenge reconstruction
│   ├── sampling.py              # stratified game/pitch selection
│   ├── quality.py               # §12.4 gates
│   ├── jev/
│   │   ├── client.py            # thin wrapper: retries, timing, versioning
│   │   └── prompts.py           # conditions A-E, PROMPT_VERSION
│   ├── baselines.py
│   ├── evaluation/
│   │   ├── metrics.py
│   │   ├── calibration.py
│   │   ├── stats.py             # clustered bootstrap, McNemar, Holm
│   │   └── latency.py
│   ├── video/                   # Part 2 — may NOT import sources/
│   └── viz.py
│
├── experiments/
│   └── exp001_part1_structured/
│       ├── config.yaml          # dataset version, conditions, seed
│       ├── run.py
│       └── README.md            # hypothesis, date, outcome
│
├── data/                        # gitignored
│   ├── raw/  interim/  processed/  video/  quarantine/
│   └── sample/                  # committed, ~50 rows
│
├── results/                     # committed: metrics JSON + figures
├── notebooks/                   # exploration only; never the source of a result
└── tests/
```

### 13.1 Tech stack

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | ecosystem |
| Env/deps | `uv` | fast, lockfile-based, reproducible |
| Schema | `pydantic` v2 | runtime validation at the source boundary — where bad data enters |
| Tabular | `polars` | fast; strict about dtypes, which surfaces silent coercion bugs |
| Storage (local) | Parquet + DuckDB | zero-setup analytics over local files |
| Storage (shared) | Supabase Postgres | queryable dataset of record; enables a future dashboard |
| HTTP | `httpx` | connection pooling, timeouts, async if needed |
| CLI | `typer` | acquisition/eval as reproducible commands, not notebooks |
| Jev | `typesafe-sdk` | official |
| Stats | `scipy`, `statsmodels` | McNemar, bootstrap, Holm |
| Baselines | `scikit-learn` | logistic regression, GBM |
| Plots | `matplotlib` | committed as PNG + the JSON behind them |
| Video | `ffmpeg` + `opencv-python` | frame extraction |
| Tests | `pytest` | quality gates are tests |

**Environment status [verified 2026-09-22]:** Python 3.12.10, uv 0.12.17, ffmpeg/ffprobe
9.0.2 and git 2.55 are installed and on PATH; `uv sync` resolves cleanly.

**Native Windows, not WSL.** WSL2 cannot start on this machine — the CPU supports
virtualization (`VMMonitorModeExtensions = True`) but it is disabled in BIOS/UEFI
(`VirtualizationFirmwareEnabled = False`). Independently of that blocker, WSL would be the
wrong choice here: the dataset lives on `S:\`, which WSL reaches over the 9P bridge at
`/mnt/s` — slow for exactly this workload (thousands of small MP4 writes and Parquet reads).
Every dependency has a native Windows build.

### 13.2 Supabase's role

Supabase holds the **processed** `PitchRecord` table and experiment results — the shareable,
queryable dataset of record. It does **not** hold video (size, licensing) or raw payloads
(bulk redistribution). Local Parquet on S: stays the working copy; Supabase is the published
artifact and the thing a future dashboard reads.

### 13.3 Determinism

Every experiment run records: dataset version hash, `PROMPT_VERSION`, Jev model version
(pinned, e.g. `jev-1.13.0`, never `jev-latest`), random seed, git commit, and UTC timestamp.
Pinning the Jev version is essential — an unannounced model update would otherwise silently
invalidate comparisons across runs.

---

## 14. Sample size

### 14.1 Spread matters more than count **[measured]**

Clustered bootstrap, 800 replicates, resampling whole games then pitches within them,
estimating the ~94 % umpire accuracy:

| Design | n | 95 % CI width |
|---|---|---|
| 7 games × 145 pitches | 1,015 | **4.53 pp** |
| 20 games × 50 | 1,000 | 3.60 pp |
| 50 games × 10 | 500 | 4.40 pp |
| **100 games × 10** | **1,000** | **3.00 pp** |
| 200 games × 5 | 1,000 | 3.00 pp |
| 100 games × 20 | 2,000 | 2.20 pp |
| 250 games × 20 | 5,000 | 1.40 pp |

The same 1,000 pitches drawn from 7 games gives a **51 % wider** interval than from 100
games. Beyond ~10 pitches per game the marginal value of more pitches from the *same* game is
small. **Spread across games is the binding constraint, not raw count.**

### 14.2 Power **[measured]**

Simulating Jev as the geometric rule plus Gaussian location noise of s.d. σ, n = 1,000 paired
pitches, McNemar two-sided at α = 0.05, 400 replicates:

| σ (in) | Jev accuracy | Umpire accuracy | Discordant pairs | Power |
|---|---|---|---|---|
| 0.25 | 99.0 % | 94.2 % | 60 | **1.00** |
| 0.5 | 97.9 % | 94.1 % | 64 | **1.00** |
| **1.0** | **95.8 %** | **94.2 %** | 73 | **0.38** |
| 2.0 | 91.5 % | 94.2 % | 105 | 0.76 |
| 3.0 | 87.5 % | 94.2 % | 139 | **1.00** |

There is a **blind spot at |Δ| ≈ 1–2 pp** — precisely the region where Jev performs about as
well as a human. n = 1,000 cannot resolve it.

**Assumption, stated explicitly:** this models Jev's errors as symmetric Gaussian noise on
location. Jev may instead fail in structured ways — biased toward BALL, or worse on breaking
balls than fastballs — which this does not capture. The simulation does get the dominant
dependence right, since umpire correctness and Jev's noise are both applied to the *same real
margin*, so their errors correlate on close pitches as they would in reality. Treat it as a
sizing guide, not a guarantee.

### 14.3 Recommendation

- **v1 evaluation set: 1,000 taken pitches from 100 distinct games**, ~10 per game.
- **Dev set: 200 taken pitches from 20 separate games**, used only for prompt development,
  never evaluated.
- **Challenge set: every ABS challenge available in the sampled window** (~0.4–0.9 per game;
  expect 40–90 from 100 games). Used for rule validation and a hard-cases head-to-head.
- **Part 2 video subset: 200 pitches**, stratified to over-sample close calls, because CV
  errors concentrate there and 200 clips ≈ 860 MB is manageable.
- **Pre-registered escalation rule (fixed now, §11.6):** if the observed |Jev − umpire|
  accuracy difference on Target A is **under 2.5 pp**, extend to 5,000 pitches from 250 games
  before drawing any conclusion. Declaring this in advance is what keeps it from being
  p-hacking.

### 14.4 Sampling strategy for v1

Deliberately simple, because complex schemes are hard to reason about and easy to bias:

1. Enumerate all 2026 regular-season games (~2,430).
2. Draw 100 games by **stratified random sampling on month** (proportional), to spread across
   the season and avoid a single hot stretch of umpiring.
3. Within each game, take all taken pitches and draw 10 by **simple random sample without
   replacement**, seeded.
4. Do **not** balance classes or over-sample close calls in the main set — the natural
   distribution is the deployment condition. Stratified *reporting* (§10.2) recovers the
   close-call view without distorting the sample.
5. Record the seed and the realised distribution over month, venue, pitcher hand, batter
   stand, count and pitch type. Report it. Do not tune it.

The close-call over-sampling happens only in the Part 2 video subset, where the reason is
cost, and it is corrected for by reporting the strata separately.

---

## 15. Phased roadmap

| Phase | Deliverable | Gate to pass |
|---|---|---|
| **0. Foundations** | Repo skeleton, `schema.py`, `groundtruth.py`, tests, `ffmpeg` installed | Ground-truth function reproduces 123/123 challenge rulings in CI |
| **1. Acquisition** | `sources/`, quality gates, 100-game pull, Parquet + Supabase | All six gates in §12.4 pass; coverage report written |
| **2. Milestone 1** | 10 games end-to-end, manually verified | See §17 |
| **3. Part 1** | Conditions A–E × targets A/B, baselines, full evaluation | Condition C ≈ 100 %, condition D ≈ base rate |
| **4. Part 2** | Overlay audit, CV pipeline, 200-pitch video run | Overlay audit clean or mitigated; module-boundary test passes |
| **5. Write-up** | `METHODOLOGY.md`, results, figures, article draft | Every number traceable to a committed results file |
| **—** | *(deferred)* Part 3, richer outcomes, live system | |

---

## 16. What must NOT be built yet

Explicitly out of scope. Building these early is the main way this project becomes a weekend
code dump instead of a study.

- Part 3 in any form.
- Outcome classes beyond BALL/STRIKE.
- A web dashboard or live demo.
- A trained ball/strike model of our own beyond the simple §7.5 baselines.
- Anything touching minor-league, spring-training or historical (pre-2026) data — the
  coordinate and zone definitions differ (§3.1) and would need separate validation.
- Multi-season analysis.
- Optimising the prompt for accuracy before the dev/eval split exists.
- Any scraper written before §5 and §18 are understood.

---

## 17. Definition of success per phase

**Phase 0** — `ground_truth()` is pure, unit-tested, and reproduces 123/123 known ABS
rulings in CI. The schema round-trips through Parquet without loss.

**Phase 1** — 100 games acquired; all §12.4 gates pass; a written coverage report gives
video availability %, challenge count, missing-field counts, and the realised sampling
distribution.

**Phase 2 (Milestone 1)** — see §20.

**Phase 3** — every cell of (5 conditions × 2 targets) filled with point estimate and
clustered-bootstrap CI; baselines reported alongside; calibration diagram and ECE produced;
latency percentiles reported; the pre-registered test family run with Holm correction.
**Success is a defensible number, not a favourable one.**

**Phase 4** — overlay audit documented; CV pipeline produces `PitchTrajectory` with reported
error vs. Statcast in inches; Part 1 vs Part 2 accuracy compared on the same pitches with the
same prompt.

**Phase 5** — a reader can reproduce every figure from the repo plus Supabase. Limitations
section explicitly states what the study does *not* show.

---

## 18. Licensing and publication

MLB's terms prohibit automated extraction **for commercial purposes**. This is non-commercial
research. The posture:

**Publish:** code, derived metrics, aggregate results, figures, `play_id` lists and links
(so others can reproduce), the processed non-video dataset.

**Do not publish:** MP4 files, bulk raw Statcast dumps, anything that functions as a
redistribution of MLB's product.

**Operationally:** rate-limit politely, cache aggressively so a re-run never re-fetches,
identify the client honestly, and stop on repeated errors rather than retrying hard.

The article links to clips on MLB's own site rather than embedding them.

---

## 19. Open questions

Tracked, not hand-waved. Each blocks a specific phase.

| # | Question | Blocks | Resolution path |
|---|---|---|---|
| 1 | Do we have Jev API access? Waitlisted; Vercel AI Gateway may be faster. | Phase 3 | Apply now; it is the longest lead time in the project |
| 2 | Do Savant clips contain overlays that leak the call? | Phase 4 | Manual audit of 20 clips (§8.2) |
| 3 | Do the `api_break_*` fields use feet or inches? | — | Re-derive movement from trajectory; avoid the fields |
| 4 | Actual Jev latency from this machine/region | Phase 3 | Measure in Phase 3; quoted 70–500 ms is the vendor's |
| 5 | Video availability season-wide (20/20 in one game) | Phase 1 | Coverage report |

**Closed during red-team** (see `PLAN_REDTEAM.md`): challenge attribution coverage (RT-1),
baseline model accuracy (RT-2), season-wide stability (RT-3), `blocked_ball` impact (RT-4),
environment availability (RT-6).

**Question 1 is the critical path.** Apply for access before Phase 0, because nothing in
Part 1 can be evaluated without it. Phases 0–2 are unblocked and independent of it.

---

## 20. Milestone 1 — the immediate next step

Small, verifiable, and a hard gate. **Do not proceed to Phase 3 until every item passes.**

**Scope:** 10 games. Roughly 1,500 taken pitches.

**Build**
1. `schema.py` — the types in §4.3, pydantic, with `location_at_plane` and `time_to`.
2. `groundtruth.py` — the rule, `margin_in`, umpire-call reconstruction, challenge identification.
3. `sources/statsapi.py` — one game → `list[PitchRecord]`, raw payload archived.
4. `sources/savant.py` — the same game from Savant, for cross-checking.
5. `sources/video.py` — `play_id` → MP4 on S:, with the `Referer` header, resumable.
6. `quality.py` — the six gates.
7. Tests, including the 123-ruling regression fixture.

**Verify — all must pass**
- [ ] StatsAPI and Savant agree on re-derived middle-of-plate location to < 0.02 ft for every pitch
- [ ] `sz_top/sz_bot` = 1.9815 ± 0.001, constant per batter per game
- [ ] Taken-pitch fraction is 50–54 %
- [ ] Class balance is 66–72 % BALL
- [ ] Every ABS challenge in the 10 games is found, `reviewType == "MJ"`, challenger recorded
- [ ] Geometric rule agrees with reconstructed `abs_call` on 100 % of those challenges
- [ ] Video downloaded for > 95 % of pitches; failures logged with reasons
- [ ] **Manual inspection of 10 clips**, written up in `docs/LOG.md`: does the clip show the
      pitch from release to plate? Is there a K-zone overlay, count/score bug, or visible
      umpire call? Can release and plate-crossing frames be identified by eye?
- [ ] Round-trip: `PitchRecord` → Parquet → `PitchRecord` is lossless
- [ ] Ten pitches spot-checked by hand against the Savant web page

**Explicitly not in Milestone 1:** any Jev call, any CV, any metric beyond the gates.

The manual clip inspection is the highest-value item. It is the only way to resolve open
question 2, and it determines whether Part 2 is straightforward or needs mitigation.
