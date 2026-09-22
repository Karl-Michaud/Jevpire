# Red-team review of `docs/PLAN.md`

**Method:** adversarial review of the plan's load-bearing claims, with each challenge
resolved by measurement rather than argument where measurement was possible. Rounds
continued until no finding changed a decision.

**Date:** 2026-09-22
**Verdict:** plan is sound after two corrections. Two findings were **critical and the plan
was wrong**; both are now fixed. Three open items remain and are external dependencies, not
design flaws.

Reproduce with:
```bash
uv run python scripts/redteam_checks.py
uv run python scripts/redteam_challenges.py       # round 1 -- shows the flaw
uv run python scripts/redteam_challenges_v2.py    # round 2 -- shows the fix
```

---

## RT-1 — CRITICAL. Ground-truth validation was run on a selected subsample. **Fixed.**

**Challenge.** §3.2 claimed "123 of 123 official ABS rulings." That validation identified the
challenged pitch using a single heuristic: the pitch whose `endTime − startTime` gap exceeds
15 s. If that heuristic fails non-randomly, the validation set is selected and the 100 %
figure is not trustworthy.

**Round 1 measurement** (135 games, 10 dates spanning April–September):

| | |
|---|---|
| Plays with `reviewType == "MJ"` | 167 |
| Attributed to a unique pitch | 81 |
| **Dropped** | **86 (51.5 %)** |

**The challenge was valid.** Coverage was 48.5 %. The original claim rested on under half the
available evidence.

**Diagnosis.** Inspecting dropped cases individually revealed two distinct failure modes:

- **(a) No pitch carries the review pause** — the pause is simply not recorded in the event
  timestamps. Accounts for most drops. In every case inspected by hand, the challenged pitch
  was the **last called pitch of the play**.
- **(b) Two pitches carry a 27.0 s pause** — more than one challenge occurred in that at-bat,
  but the API stores only **one** `reviewDetails` object. Genuinely ambiguous and
  irrecoverable.

**Fix.** A tiered attribution rule:

```
tier 1  exactly one called pitch with a review pause > 15 s   -> that pitch
tier 2  no pitch with a pause                                 -> last called pitch of the play
drop    more than one paused pitch                            -> ambiguous, exclude
```

**Round 2 measurement:**

| Tier | n | % of MJ plays | Agreement with official ABS ruling |
|---|---|---|---|
| tier 1 — unique review pause | 81 | 48.5 % | **81/81 = 100.0 %** |
| tier 2 — last called pitch | 83 | 49.7 % | **83/83 = 100.0 %** |
| drop — multiple challenges, one record | 3 | 1.8 % | — |
| **attributed** | **164** | **98.2 %** | **164/164 = 100.0 %** |

Overturn rate across the attributed set: 48.2 %.

**Why this resolves the selection worry rather than merely improving coverage.** Tier 2
identifies *different pitches* by a *different rule* than tier 1, and reaches 100 %
independently. If the tier-1 subsample had been biased toward cases the rule handles well,
tier 2 would have exposed it. It did not.

**Plan changes:** §3.2 and §6.5 rewritten. Quality gate 3 in §12.4 now checks per-tier
agreement and the attribution rate, so a future drop in either is caught.

---

## RT-2 — CRITICAL. The baseline claim was wrong. **Fixed.**

**Challenge.** §7.5 marked "logistic regression on 4 coordinates ≈ 99 %" as **[assumed]**.
That assumption is load-bearing: it sets the bar Jev must clear. If it is wrong, the whole
interpretation of Part 1 is wrong.

**It is wrong.** Measured on 24,619 taken pitches across 160 games, group-split by game 70/30:

| Model | Features | Target A (rule) | Target B (human call) |
|---|---|---|---|
| Majority class | — | 69.28 % | 68.49 % |
| **Logistic regression** | raw coords | **69.28 %** | **68.49 %** |
| **Logistic regression** | engineered | **71.47 %** | 71.64 % |
| HistGradientBoosting | raw coords | **99.69 %** | 94.94 % |
| HistGradientBoosting | engineered | **99.95 %** | 94.98 % |
| Geometric rule | — | 100 % by construction | 94.84 % |
| Human umpire | — | 94.75 % | 100 % by construction |

Logistic regression on raw coordinates scores **exactly the majority-class rate** — it learns
nothing at all.

**Why.** The strike region is `|x| ≤ c AND z ≤ top AND z ≥ bot`: an axis-aligned **box**, i.e.
an intersection of half-spaces. A linear classifier produces a single hyperplane and cannot
represent either the absolute value or the conjunction. Supporting evidence:

```
corr(plate_x,        is_strike) = -0.0103      <- raw: no linear signal
corr(|plate_x|,      is_strike) = -0.4671      <- engineered: strong
```

Engineering `|plate_x|`, `sz_top − plate_z`, `plate_z − sz_bot` fixes the absolute value but
not the conjunction, which is why engineered features only reach 71 %. Tree ensembles split
on axis-aligned thresholds, which is exactly the right inductive bias for a box — hence
99.95 %.

**Plan changes:** §7.5 baseline table replaced with measured values. The correct bar for Jev
on Target A is **HistGradientBoosting at 99.7 %**, not a fictional linear model. The
contrast is also a genuinely useful teaching result about inductive bias, and belongs in the
write-up.

---

## RT-3 — Are the headline numbers an artefact of one week in June? **Resolved: no.**

**Challenge.** Every quantitative claim in the first draft came from a single 3-day window.
Umpire crews rotate, and zone enforcement could drift over a season.

**Measurement** — four windows spanning the season:

| Window | Games | Pitches | Taken % | BALL class % | Umpire acc % | Within 1 in % |
|---|---|---|---|---|---|---|
| Apr 20–22 | 40 | 12,069 | 53.4 | 69.1 | 94.67 | 11.3 |
| Jun 15–17 | 39 | 11,163 | 51.9 | 68.7 | 94.20 | 11.0 |
| Aug 10–12 | 40 | 11,724 | 52.2 | 70.1 | 95.18 | 10.9 |
| Sep 05–07 | 41 | 12,088 | 51.7 | 69.2 | 94.93 | 11.3 |

Stable. Spread is ~1 pp on umpire accuracy and under 1.5 pp on the others. The §12.4 quality
gate bands (50–54 % taken, 66–72 % BALL) comfortably contain all four windows.

**Plan change:** none needed. Numbers upgraded from single-window to four-window.

---

## RT-4 — Does `blocked_ball` pad the accuracy with free wins? **Resolved: negligibly.**

**Challenge.** `blocked_ball` (balls in the dirt) are taken pitches, but if they are all
trivially balls they inflate every method's accuracy and make the task look easier.

**Measurement** (24,619 taken pitches):

| description | n | % geometric strike | mean abs margin |
|---|---|---|---|
| ball | 15,870 | 3.3 % | 7.1 in |
| called_strike | 7,798 | 90.2 % | 3.5 in |
| **blocked_ball** | **951** | **0.0 %** | **14.9 in** |

The challenge is real in kind — 951 pitches, none of them strikes, averaging 14.9 inches
outside the zone — but small in magnitude:

```
umpire accuracy including blocked_ball : 94.75 %
umpire accuracy excluding blocked_ball : 94.54 %
sensitivity                            :  0.21 pp
```

**Decision: include them, and report the sensitivity.** They are genuine taken pitches that
the umpire judged, and excluding a category *after* seeing that it is easy is the kind of
post-hoc choice this project is trying to avoid. The 0.21 pp figure is recorded so no reader
has to wonder.

**Plan change:** §12.3 now states the decision and the measured sensitivity explicitly.

---

## RT-5 — New finding: Target B is far harder than the plan assumed.

Not a flaw, but it changes what counts as an interesting result.

Predicting **the umpire's actual call** from location:

| Predictor | Accuracy |
|---|---|
| Geometric rule | 94.84 % |
| HistGradientBoosting, raw coords | 94.94 % |
| HistGradientBoosting, engineered | 94.98 % |

A gradient-boosted tree with thousands of training pitches beats "just apply the rulebook" by
**0.1 pp**. Umpire deviations from the ABS zone are very close to unpredictable from pitch
location alone — at least without catcher, count and framing features.

**Implication.** Target B is a genuinely hard benchmark with a near-irreducible error floor
around 5 %. If Jev reaches ~95 % on Target B it has matched a tuned GBM; anything above ~95.5 %
would be a real and surprising finding worth investigating hard for leakage before believing.

**Plan change:** §7.5 and §17 now state the Target B ceiling so it is not mistaken for
underperformance.

---

## RT-6 — Environment claims were unverified. **Resolved.**

The plan listed a tech stack without checking it existed.

| Tool | Status at review | Now |
|---|---|---|
| Python | **not installed** (Store alias stub only) | 3.12.10 |
| uv | not installed | 0.12.17 |
| ffmpeg / ffprobe | not installed | 9.0.2 |
| git | installed but **not on PATH** | on persistent user PATH |
| WSL2 | **blocked** — virtualization disabled in firmware | see below |

WSL2 cannot start on this machine: `VMMonitorModeExtensions = True` (CPU supports it) but
`VirtualizationFirmwareEnabled = False` (disabled in BIOS/UEFI). The Virtual Machine Platform
component has been enabled and awaits a reboot, but virtualization must still be switched on
in firmware by hand.

**Decision: build native Windows, not WSL.** Beyond the firmware blocker, the dataset lives on
`S:\` and WSL would reach it through the 9P bridge at `/mnt/s`, which is slow for exactly the
workload here — thousands of small MP4 writes and Parquet reads. Every dependency has a
native Windows build.

---

## Findings raised and dismissed

**"The 100 % validation is circular — ABS and our rule use the same Hawk-Eye measurement."**
Partly true and already stated in §6.3. The validation establishes that our *specification*
(plane, ball radius, zone derivation) matches MLB's, which is what it claims. It cannot
validate Hawk-Eye itself, and §6.3 quantifies the residual risk: with median tracking error
0.16 in and 5.2 % of pitches within 0.5 in of an edge, roughly 1–3 % of taken pitches could
carry a flipped label, concentrated entirely in the close-call stratum. Stated as a
limitation; not fixable with public data.

**"The power analysis models Jev as the rule plus Gaussian noise, which is unrealistic."**
Correct, and now labelled as an assumption in §14.2. Jev may fail in structured ways — biased
toward BALL, or worse on breaking balls — rather than with symmetric noise. The simulation
does capture the dominant dependence correctly, since umpire correctness and Jev noise are
both applied to the same real margin, so their errors correlate on close pitches as they
would in reality. Treated as a sizing guide, not a guarantee.

**"Umpire accuracy of 94.75 % is not 'umpire correctness'."** Correct, and worth stating in
the write-up. Umpires are trained to the *rulebook* zone — three-dimensional, stance-dependent
— and are being scored here against the *ABS* zone, which is a flat plane and ignores stance.
The number is **agreement with the ABS zone**, not a measure of professional competence.
Added as an interpretation note to §10.3.

---

## Open items — external dependencies, not design flaws

| # | Item | Blocks | Why it cannot be closed now |
|---|---|---|---|
| 1 | **Jev API access** — waitlisted; Vercel AI Gateway may be faster | Phase 3 | Vendor-controlled. **Longest lead time in the project — apply before Phase 0.** |
| 2 | **Video overlay audit** — do clips leak the call via a K-zone graphic, count bug, or visible umpire signal? | Phase 4 | Requires human eyes on ~20 clips. Gated in §8.2 and Milestone 1. |
| 3 | **Measured Jev latency** from this machine and region | Phase 3 | The 70–500 ms figure is the vendor's claim, not a measurement. |

---

## Convergence

Round 1 produced two critical findings (RT-1, RT-2). Round 2 verified both fixes by
measurement and produced no new findings that changed a decision. RT-3 through RT-6 were
resolved without altering the design. The remaining three items are external dependencies.

**The plan is considered converged for Phases 0–2.** Phase 3 cannot be finalised until open
item 1 resolves; Phase 4 cannot until open item 2 does.
