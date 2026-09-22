# Jevpire

**Evaluating fast probabilistic decision-making in baseball.**

Can [Jev](https://typesafe.ai) — TypeSafe AI's "System One" decision model — call balls and
strikes? And if it can, is it *honest* about when it isn't sure, and is it fast enough to
matter?

> **Status:** planning complete and red-teamed. No experiments run yet.
> Start with [`docs/PLAN.md`](docs/PLAN.md).

---

## Why this problem

Jev is not an LLM. It takes structured state and typed questions, and returns typed values
with calibrated probabilities and confidence — no generated text. Its three headline claims
are accuracy, calibration, and 70–500 ms latency.

Calling pitches tests all three at once:

- **Objective rule.** MLB's 2026 Automated Ball-Strike zone is published and precise, so
  correctness is not a matter of taste.
- **Strong human baseline.** Home-plate umpires agree with that zone **94.75 %** of the time.
- **Physically meaningful latency.** A pitch is in flight for 390–450 ms — the same order as
  Jev's quoted response time.

The project is not trying to make Jev win. `Human > Jev` is a publishable result.

---

## Research questions

1. **Accuracy** — how well does Jev classify taken pitches, against the rule, the umpire, and
   real baselines?
2. **Calibration** — when Jev says 80 %, is it right 80 % of the time?
3. **Latency** — the full distribution, not the mean.
4. **Information sufficiency** — how does accuracy degrade as information is withheld?
5. **Vision** — can a CV model recover enough geometry from broadcast video to do the same job?

Deferred: **how early** in the flight a correct call can be made.

---

## Ground truth

A pitch is a strike iff, at the **middle** of home plate (y = 0.70833 ft):

```
|x| ≤ 0.70833 + 0.12083          half-plate 8.5"  +  ball radius 1.45"
 z  ≤ sz_top  + 0.12083
 z  ≥ sz_bot  − 0.12083
```

Validated against **164 of 164** official ABS rulings across 135 games spanning April–September
2026, using two independent attribution methods that each reached 100 % separately. Competing
specifications fail clearly — front-of-plate with no ball radius scores 72 %.

See [`docs/PLAN.md` §6](docs/PLAN.md) and [`docs/DATA_DICTIONARY.md` §4](docs/DATA_DICTIONARY.md).

---

## What's here

| Document | What it is |
|---|---|
| [`docs/PLAN.md`](docs/PLAN.md) | **Single source of truth.** Design, methodology, statistics, roadmap. |
| [`docs/PLAN_REDTEAM.md`](docs/PLAN_REDTEAM.md) | Adversarial review of the plan. Two critical errors found and fixed. |
| [`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md) | 2026 MLB pitch data, verified against the live APIs. |
| [`docs/LOG.md`](docs/LOG.md) | Append-only research log. |
| `scripts/redteam_*.py` | Reproduce every measured claim in the plan. |

---

## Reproducing the measurements

```bash
uv sync --all-extras
uv run python scripts/redteam_checks.py            # baselines, class balance, difficulty
uv run python scripts/redteam_challenges_v2.py     # ground-truth validation vs official ABS
```

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and ffmpeg (for later video work).

---

## Data and licensing

MLB's terms prohibit automated extraction **for commercial purposes**. This is non-commercial
research.

**Published:** code, derived metrics, aggregate results, figures, `play_id` lists so others can
reproduce.
**Not published:** MP4 clips, bulk raw Statcast dumps — anything that would function as a
redistribution of MLB's product. Video stays local and is gitignored.

---

## License

See [LICENSE](LICENSE).
