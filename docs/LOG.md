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

**Next step.** Phase 1 — the acquisition pipeline, gathering everything every experiment will
need, including video.
