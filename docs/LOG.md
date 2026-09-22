# Research log

Append-only. Newest entries at the bottom. Nothing about methodology changes without an
entry here — especially not after results have been seen.

**Entry template**

```
## YYYY-MM-DD — short title

**Hypothesis / question**
**Configuration** (dataset version, PROMPT_VERSION, jev model, seed, git commit)
**What I did**
**Result**
**Observations / surprises**
**Decision and next step**
```

---

## 2026-09-22 — Project scoped; data foundation researched and validated

**Question.** Is a Jev ball/strike study feasible on publicly available 2026 MLB data, and
what is the defensible ground truth?

**What I did.** Researched Jev (TypeSafe AI System One model, released 2026-09-15: text/JSON
state in, typed decisions with calibrated probabilities out, no image input). Characterised
the 2026 MLB pitch data directly against the live APIs rather than from documentation.

**Results.**
- Two silent 2026 redefinitions found: `plate_x`/`plate_z` moved from front-of-plate to
  middle-of-plate, and `sz_top`/`sz_bot` are now the ABS zone (0.535 / 0.270 × measured
  height). StatsAPI still reports front-of-plate while Savant reports middle — they disagree
  by up to ~1.2 in.
- Trajectory parameters are anchored at y = 50 ft, *not* at the release point. Reconstructing
  correctly reproduces Savant's published `plate_x` to 0.06 in; doing it naively is off by
  0.45 ft.
- Ground-truth rule established and validated: 164/164 official ABS rulings.
- ABS coverage is ~0.3 % of pitches (~1.24 challenges/game), so ABS is a hard-cases
  comparator, not a general label source.
- Video: 1280×720, 59.57 fps, ~7.5 s, ~4.3 MB/pitch, 20/20 available in a sampled game.

**Surprises.**
- `details.call.code` on a challenged pitch is the **post-challenge corrected** call. Assuming
  otherwise gives 58 % agreement with the geometric rule — a coin flip, which is how the bug
  announced itself.
- Statcast trajectories are fitted to the *completed* flight, so Part 3 cannot be done
  honestly from Statcast alone. This is why video is collected from the start.

**Decision.** Parts 0–2 proceed on already-available data. Part 3 deferred but its data
requirements are locked into Part 0. Plan written to `docs/PLAN.md` as SSOT.

---

## 2026-09-22 — Red-team round 1 and 2; plan converged for Phases 0–2

**Question.** Does the plan survive adversarial review?

**What I did.** Attacked the plan's load-bearing claims, resolving each by measurement where
possible. Scripts: `scripts/redteam_checks.py`, `scripts/redteam_challenges.py`,
`scripts/redteam_challenges_v2.py`.

**Results — two critical findings, both plan errors, both fixed.**

- **RT-1.** The ground-truth validation used a challenge-attribution heuristic with only
  **48.5 % coverage** — a selected subsample. Diagnosed two failure modes (no recorded review
  pause; two challenges sharing one `reviewDetails`). Replaced with a tiered rule: 98.2 %
  coverage, **164/164** agreement, and — decisively — the two tiers use different rules on
  different pitches and each reach 100 % independently, which is what rules out selection bias.
- **RT-2.** The plan assumed a logistic-regression baseline of ~99 % on Target A. Measured:
  **69.28 %, exactly the majority-class rate.** The strike region is an axis-aligned box, and a
  linear model cannot represent an absolute value or a conjunction. The correct baseline is
  HistGradientBoosting at **99.69 %**. The bar for Jev moved accordingly.

**Also established.** Headline numbers stable across four season windows (RT-3).
`blocked_ball` sensitivity is 0.21 pp — kept, with the number reported (RT-4). Target B has a
near-irreducible ~5 % floor: a tuned GBM beats the plain rulebook rule by only 0.1 pp at
predicting the human call (RT-5). Toolchain installed and verified; native Windows chosen over
WSL, which is blocked by BIOS virtualization anyway (RT-6).

**Observation.** Both critical findings were assumptions the plan had *labelled* as assumptions
and then reasoned from as if settled. The `[measured]` / `[assumed]` convention earned its keep
immediately.

**Decision.** Plan converged for Phases 0–2. Open items are all external: Jev API access
(blocks Phase 3, longest lead time — apply first), video overlay audit (blocks Phase 4),
measured Jev latency.

**Next step.** Round 3 — a fresh sweep, since round 2 verified fixes rather than attacking
new surface.

---

## 2026-09-22 — Red-team rounds 3 and 4; Part 2 deferred; plan converged

**Question.** Does the revised plan survive a fresh attack on material rounds 1–2 never
touched?

**Round 3 — five findings, three decision-changing.**
- **RT-8:** the pre-registered escalation target of 5,000 pitches reaches only 0.66–0.86
  McNemar power in the 1–2 pp band it exists to resolve. Raised to 10,000; the plan now leads
  with the paired effect size and its interval rather than a binary significance verdict.
- **RT-10:** extracted frames and found the clips leak the outcome — the score bug reads
  `0-0` at frame 30 and `1-0` at frame 440. The call, in plain text, in frame.
- **RT-11:** three clips from three ballparks show three different camera angles. On side-on
  feeds `plate_x` lies along the camera depth axis. The ±2-inch target was abandoned.
- RT-7 validated the condition-D tripwire (threshold set at 72 %); RT-9 found the season is
  not over (2,341 of 2,458 games Final).

**Round 4 — four findings, one decision-changing.**
- **RT-17:** tested the RT-11 rescope and it fails too. Naive frame differencing gives 79–245
  candidates/frame; a proper detector with field mask, brightness and kinematic gates still
  gives 32–78, and the recovered "track" sits on the pitcher's glove three seconds before the
  pitch. Classical ball tracking does not work on broadcast video.
- RT-12: the ABS zone is *perfectly* stable per batter across games — 407/407, 0.000 in.
- RT-15: data loss is 0.24 %, all-or-nothing per pitch.
- RT-16: game and umpire clustering are equivalent at v1 scale but diverge at 500 games.

**Decision.** **Part 2 deferred** (option A of §8.5). v1 = Part 0 + Part 1. Video is still
collected, so Part 2 resumes later from an existing dataset. This resolves round 4's only
decision-changing finding, and the plan is **converged**.

**Observations.** Decision-changing findings went 2 → 0 → 3 → 1. Round 2 didn't count — it
verified fixes rather than attacking fresh surface, and round 3 then found three problems in
material round 2 never examined. Two patterns worth keeping: assumptions the plan itself
labelled `[assumed]` were reasoned from as if settled (RT-2, RT-8), and the findings that
mattered most came from *opening the artefact* rather than reasoning about it (RT-10, RT-11
were invisible until frames were extracted and looked at).

**Next step.** Phase 0 → Phase 1: the acquisition pipeline, gathering everything every
experiment will need, video included. **In parallel: apply for Jev API access** — it is
waitlisted and now the only open item between v1 and a result.
