# Data Dictionary — 2026 MLB pitch data

Everything here was verified empirically against the live APIs on 2026-09-22, not taken
from documentation. Where the official docs disagree with the data, the data wins and the
discrepancy is flagged.

---

## 1. The coordinate system

All Statcast tracking lives in one right-handed 3D frame, in **feet**:

```
        z  (up)
        |
        |
        +--------- y  (from home plate toward the pitcher's mound)
       /
      x  (horizontal, across the field)
```

- **Origin** = the back tip of home plate, at ground level.
- **y** increases toward the mound. The pitching rubber is at y = 60.5 ft.
  The *front edge* of home plate is at y = 17/12 = **1.41667 ft**.
  The *middle* of home plate is at y = 8.5/12 = **0.70833 ft**.
- **z** is height above the ground. z = 0 is the dirt.
- **x** is side-to-side. Sign convention, verified on 2,791 pitches:

  | Observation | Mean x |
  |---|---|
  | Right-handed pitchers' release point | **−1.78** |
  | Left-handed pitchers' release point | **+1.70** |
  | Hit-by-pitch, right-handed batter | **−2.05** |
  | Hit-by-pitch, left-handed batter | **+2.30** |

  So **negative x = third-base side** (where a RHB stands), **positive x = first-base
  side** (where a LHB stands). This is the "catcher's perspective" used in every Savant
  strike-zone plot: you are behind the plate looking out at the mound, and the RHB is on
  the left of the picture.

Because the ball is always travelling toward the plate, **`vy0` is always negative**.

---

## 2. The trajectory model — the most important thing in this file

Statcast does not store the ball's full flight path. It stores a **nine-number
constant-acceleration fit**, and every location field you see is derived from it.

```
x(t) = x0 + vx0·t + ½·ax·t²
y(t) = y0 + vy0·t + ½·ay·t²
z(t) = z0 + vz0·t + ½·az·t²
```

The critical, easy-to-miss detail: **this fit is anchored at y = 50 ft, not at the release
point.** t = 0 is the moment the ball crosses the y = 50 plane. Release happens at
y ≈ 53–55 ft, which is *before* that — i.e. at **negative t**.

| Symbol | Meaning | Units | Typical |
|---|---|---|---|
| `vx0` `vy0` `vz0` | Velocity components at y = 50 | ft/s | vy0 ≈ −120 to −145 |
| `ax` `ay` `az` | Acceleration components (drag + Magnus + gravity) | ft/s² | ay ≈ +25 to +32, az ≈ −10 to −35 |
| `x0` `y0` `z0` | Position at the y = 50 plane | ft | y0 = 50.000 |

`ay` is **positive** because the ball moves in the −y direction and drag opposes motion.
`az` is gravity (−32.17) plus the Magnus lift from backspin, so a riding four-seamer has
`az` around −10 to −15 and a curveball around −40.

**To get the ball's position at any plane y = Y:** solve the quadratic for t, take the root
on the flight path, then evaluate x(t) and z(t). This single function is the backbone of
the whole project — Part 3 (pre-emptive prediction) is literally "evaluate this at
progressively earlier t."

### Source gotcha: the two APIs anchor this differently

| | Anchored at y = 50? | Release point given separately? |
|---|---|---|
| **StatsAPI** `pitchData.coordinates` | Yes — `x0`, `y0`(=50.000), `z0` | No |
| **Savant CSV** | No — gives `release_pos_x/y/z` at the *actual* release plane | Yes |

So with the Savant CSV you must first back-solve for the y = 50 anchor before you can
evaluate anywhere else. Verified: doing this reproduces Savant's own published `plate_x` /
`plate_z` to **0.005 ft (0.06 in)** — pure rounding error. Skipping the step and naively
treating `release_pos_*` as the anchor gives 0.45 ft of error, which is larger than the
entire ball.

Also: `release_pos_y = 60.5 − release_extension`, exactly.

---

## 3. What changed in 2026 — read this before touching any pre-2026 data

Two silent, breaking redefinitions landed with the ABS rollout.

### 3.1 `plate_x` / `plate_z` moved from the front of the plate to the middle

Through 2025 these were measured where the ball crosses the **front edge** (y = 1.41667).
From 2026 they are measured at the **middle** (y = 0.70833), to match where ABS adjudicates.

A ball falls roughly 0.7–1.2 inches over those 8.5 inches of travel. That is small in
absolute terms and decisive for close calls.

### 3.2 `sz_top` / `sz_bot` are now the ABS zone, not an operator's eyeball estimate

Through 2025 a human stringer set the zone per plate appearance, so it wobbled. From 2026
it is computed from the batter's independently measured height:

```
sz_top = 0.535 × height
sz_bot = 0.270 × height
```

Verified: `sz_top / sz_bot` = 1.9815 on every pitch, which is exactly 53.5 / 27, and the
values are **constant for a given batter across the whole game**. The implied height is
usually an inch or two off the roster listing, because MLB re-measured every player.

### 3.3 The two sources still disagree — pick one and normalize

`statsapi` `pitchData.coordinates.pX` / `pZ` are **still front-of-plate**. Savant's
`plate_x` / `plate_z` are middle-of-plate. Confirmed by re-solving the trajectory and
matching to 3 decimals. They differ by up to ~1.2 inches vertically.

`strikeZoneTop` / `strikeZoneBottom` in StatsAPI **do** match Savant's `sz_top` / `sz_bot`
to 4 decimals. Only the pitch location differs.

> **Rule for this project:** never consume `plate_x`/`plate_z`/`pX`/`pZ` directly. Always
> re-derive location from the nine trajectory parameters at an explicitly named plane.

---

## 4. Ground truth — the ABS zone rule

A pitch is a **strike** iff, at the middle of the plate (y = 0.70833):

```
|x|  ≤  0.70833 + 0.12083          (half-plate 8.5" + ball radius 1.45")
 z   ≤  sz_top  + 0.12083
 z   ≥  sz_bot  − 0.12083
```

The zone is a flat 2D rectangle, 17 inches wide, and **any part of the ball touching it is
a strike** — hence the ball-radius term on all four edges.

**Validation:** tested against every real ABS ruling obtainable from two independent
windows of the 2026 season — 79 challenges from one week in June, 44 from April / August /
September. **123 / 123 exact agreement.** Ten of those pitches sat within half an inch of a
zone edge and the rule still got all ten right.

Competing hypotheses fail clearly, which is what makes this a real test rather than a
lucky fit:

| Plane | Ball radius | Agreement |
|---|---|---|
| **middle** | **1.45 in** | **100.0 %** |
| middle | 0.72 in | 81.0 % |
| middle | 0 | 60.8 % |
| front | 1.45 in | 94.9 % |
| front | 0.72 in | 86.1 % |
| front | 0 | 72.2 % |

---

## 5. Field reference

### 5.1 Identity and joining

| Field | Meaning |
|---|---|
| `game_pk` | Unique game id. The join key to StatsAPI. |
| `at_bat_number` | 1-based index of the plate appearance within the game. |
| `pitch_number` | 1-based index of the pitch within the plate appearance. |
| `playId` | **StatsAPI only.** UUID for the pitch. The *only* key to video. |
| `batter` / `pitcher` | MLB player ids. |
| `stand` / `p_throws` | Batter stance / pitcher handedness, `L` or `R`. |

**The Savant CSV has no `playId`.** To attach video you must join
`(game_pk, at_bat_number, pitch_number)` → StatsAPI `playEvents` to recover it.

`sv_id` and `umpire` exist as columns but are **empty in 2026** — both deprecated. The home
plate umpire comes from StatsAPI `liveData.boxscore.officials`.

### 5.2 Location and zone

| Field | Meaning | Units |
|---|---|---|
| `plate_x` / `plate_z` | Ball position at the middle of the plate (2026+) | ft |
| `sz_top` / `sz_bot` | ABS zone top / bottom | ft above ground |
| `zone` | Coarse grid cell: 1–9 = 3×3 inside the zone, 11–14 = the four outside quadrants | — |

### 5.3 Velocity, spin and movement

| Field | Meaning | Units |
|---|---|---|
| `release_speed` | Speed out of the hand | mph |
| `effective_speed` | Speed adjusted for how far in front the ball is released | mph |
| `release_extension` | How far toward the plate the ball is released, from the rubber | ft |
| `release_spin_rate` | Spin | rpm |
| `spin_axis` | Spin direction in the x–z plane. 180° = pure backspin, 0° = pure topspin | degrees |
| `arm_angle` | Angle of the line from throwing shoulder to ball at release | degrees |
| `pfx_x` / `pfx_z` | Movement vs. a hypothetical spinless pitch, over the last 40 ft | ft |
| `api_break_x_arm` | Same horizontal movement, re-signed so positive = toward the pitcher's arm side | see note |
| `api_break_x_batter_in` | Same, re-signed so positive = toward the batter | see note |
| `api_break_z_with_gravity` | Total vertical drop including gravity | see note |

> **Note on the `api_break_*` family:** the official docs say inches, but the observed
> values sit on the same scale as `pfx_x` (range ±1.76), and `api_break_x_arm ≈ −pfx_x`
> exactly. Treat the units as unconfirmed and re-derive movement from the trajectory
> instead if it matters.

> **Do not mix `pfx` across sources.** StatsAPI `pfxX` and Savant `pfx_x` are on different
> scales *and* the ratio is not constant (observed 5.4–7.2 across pitches), so it is not a
> unit conversion — they are measuring over different reference distances.

### 5.4 Outcome

Three fields describe what happened, at three different granularities.

**`type`** — one character, the coarsest:

| Code | Count in sample | Meaning |
|---|---|---|
| `B` | 999 | Ball |
| `S` | 1296 | Strike (any kind) |
| `X` | 496 | Ball put in play |

**`description`** — the pitch-level result. Full observed vocabulary for one day (2,791 pitches):

| Value | n | Batter swung? | Umpire judgment? |
|---|---|---|---|
| `ball` | 927 | no | **yes** |
| `called_strike` | 460 | no | **yes** |
| `blocked_ball` | 58 | no | **yes** |
| `foul` | 496 | yes | no |
| `hit_into_play` | 496 | yes | no |
| `swinging_strike` | 298 | yes | no |
| `foul_tip` | 25 | yes | no |
| `swinging_strike_blocked` | 11 | yes | no |
| `hit_by_pitch` | 11 | no | no |
| `foul_bunt` | 5 | yes | no |
| `missed_bunt` | 1 | yes | no |
| `automatic_ball` | 3 | no | **no — not a judgment call** |

**`events`** — the plate-appearance outcome, populated only on the final pitch of a PA:
`field_out`, `strikeout`, `single`, `walk`, `home_run`, `double`, `force_out`,
`hit_by_pitch`, `grounded_into_double_play`, `field_error`, `triple`, `sac_fly`, …

**`des`** — free-text description of the play. Contains commas; parse the CSV properly.

> **The taken-pitch set** — the only pitches where a ball/strike decision was actually
> made — is `ball` + `called_strike` + `blocked_ball` ≈ **52 % of all pitches**.
> `automatic_ball` (pitch-clock violations, automatic intentional walks) must be
> **excluded**: no one judged anything. `hit_by_pitch` is also excluded — the outcome is
> determined by contact, not by location.

### 5.5 Count and game state

`balls` and `strikes` are the count **before** this pitch. Also available: `outs_when_up`,
`inning`, `inning_topbot`, `on_1b` / `on_2b` / `on_3b` (runner player ids or empty),
`home_score` / `away_score`, `delta_run_exp` (change in run expectancy), and
`delta_home_win_exp`.

### 5.6 Batted-ball and swing fields

Populated only when relevant: `launch_speed`, `launch_angle`, `hit_distance_sc`, `bb_type`,
`hc_x` / `hc_y` (landing coordinates), `estimated_ba_using_speedangle` and its siblings
(expected outcomes from exit velocity + launch angle), and the 2024+ bat-tracking fields
`bat_speed`, `swing_length`, `attack_angle`, `attack_direction`, `swing_path_tilt`.

None of these are inputs for Part 1 — they are all post-contact, and using them would be
textbook leakage.

---

## 6. StatsAPI-only fields

These have no Savant CSV equivalent and are why the pipeline needs both sources.

| Field | Meaning |
|---|---|
| `playId` | Pitch UUID → video |
| `startTime` / `endTime` | ISO-8601 timestamps, millisecond precision |
| `pitchData.plateTime` | Flight time from release to plate, in seconds (~0.38–0.45) |
| `details.call.code` | `B` ball, `C` called strike, `S` swinging strike, `F` foul, `D` in play, `*B` ball in dirt |
| `reviewDetails` | Challenge record, attached at the **play** level, not the pitch level |
| `gameData.absChallenges` | Per-team challenge counters: `usedSuccessful`, `usedFailed`, `remaining` |
| `liveData.boxscore.officials` | Umpire crew, including `Home Plate` |

### 6.1 ABS challenges — two traps

**Trap 1: `details.call.code` on a challenged pitch is the *corrected* call.** MLB rewrites
the record after an overturn. The umpire's *original* call has to be reconstructed:

```
abs_call     = details.call.code                       # already corrected
umpire_call  = flip(abs_call) if isOverturned else abs_call
```

Getting this backwards produces ~58 % agreement with the geometric rule — a coin flip —
which is how the bug announces itself.

**Trap 2: `reviewDetails` sits on the play, not the pitch.** To find *which* pitch was
challenged, use the review pause: the challenged pitch has an `endTime − startTime` gap of
**exactly 27.0 s** versus ~3–5 s for a normal pitch. This held on 78 of 79 challenges in
the June sample.

`reviewType` distinguishes the kind of review: **`MJ` = ABS pitch challenge**,
`MF` / `MA` = manager challenge of a field call, `NH` = boundary review. Filter to `MJ`.

### 6.2 ABS coverage is sparse — plan around it

ABS only rules on pitches someone challenges.

| Sample | Games | ABS challenges | Per game |
|---|---|---|---|
| Jun 15–21 | 90 | 79 | 0.88 |
| Apr / Aug / Sep | 110 | 44 | 0.40 |

That is roughly **0.3 % of all pitches**. A full 2026 season yields only ~2,000 ABS
verdicts, and they are a heavily biased sample — close calls only, with a 36–44 % overturn
rate.

**Consequence:** "Jev vs. ABS" cannot be measured on a random sample of pitches. It is a
separate, small, hard-cases evaluation. The geometric rule in §4 is what provides a label
for every pitch, and the challenge set is what *validates* that rule.

---

## 7. Video

| Property | Value |
|---|---|
| URL | `https://baseballsavant.mlb.com/sporty-videos?playId=<uuid>` |
| MP4 | in a `<source>` tag on that page; host is `sporty-clips.mlb.com` |
| Requires | `Referer: https://baseballsavant.mlb.com/`, else 403 |
| Resolution | 1280 × 720 |
| Frame rate | 450 frames / 7.554 s = **59.57 fps** (broadcast 59.94) |
| Duration | ~7.5 s |
| Size | ~4.3 MB per pitch |
| Availability | 20 / 20 sampled pitches from a regular-season game |

The signed MP4 URLs expire — re-resolve from the `playId` rather than caching the link.

**Frame rate sets the hard floor on Part 3.** One frame = 16.8 ms. A pitch's flight is
~390–450 ms, so the entire flight is only **~24 frames**. "100 ms before the plate" means
dropping 6 frames.

Storage: 4.3 MB × 1,000 pitches ≈ 4.3 GB; × 10,000 ≈ 43 GB. A full season of ~700k pitches
would be ~3 TB.

**Unverified — must be checked by eye before any video experiment:** whether the clips
carry a broadcast strike-zone overlay, a count/score bug, or a visible umpire signal. Any
of those is outcome leakage straight into the vision model.

---

## 8. Licensing

MLB's terms prohibit automated extraction **for commercial purposes**. This project is
non-commercial research. The safe posture:

- **Do** publish code, derived metrics, aggregate results, and plots.
- **Do** publish `playId`s and links so others can reproduce.
- **Do not** redistribute MP4 files or raw bulk Statcast dumps in the repo.
- Rate-limit politely. Cache locally so a re-run never re-fetches.

---

## 9. The normalized record

Everything above collapses to one source-agnostic structure. Any acquisition backend —
Savant CSV, StatsAPI, a future CV pipeline reading pixels — produces *this*, and every
downstream stage consumes only this.

```
PitchTrajectory          # the physics, anchored at y = 50
  x0, y0, z0
  vx0, vy0, vz0
  ax, ay, az
  → location_at(y) -> (x, z)
  → time_to(y)     -> t

StrikeZone
  top, bottom                   # ft above ground
  half_width = 0.70833
  ball_radius = 0.12083

PitchRecord
  pitch_id, game_pk, at_bat_number, pitch_number, play_id
  trajectory: PitchTrajectory
  zone: StrikeZone
  context: count, outs, inning, batter/pitcher ids and handedness
  umpire_call: BALL | STRIKE                # reconstructed, pre-challenge
  abs_call:    BALL | STRIKE | None         # only on challenged pitches
  ground_truth: BALL | STRIKE               # geometric rule, §4
  provenance: source, fetched_at, schema_version
```

The `provenance` block is not bureaucracy. The whole point of the interface is that a CV
pipeline can later populate `trajectory` from pixels instead of from Statcast, and the
evaluation code must not be able to tell the difference — but the *analysis* must be able
to, in order to compare them.
