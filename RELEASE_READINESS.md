# Release Readiness

Two numbers decide whether zipmonkey is shippable:

- **Release-Readiness Score (RRS, 0–100)** — a snapshot of quality *now*.
- **Convergence** — the *trend* (across review panels) that says the snapshot is
  trustworthy, i.e. that few undiscovered defects remain.

They answer different questions. A high RRS with low convergence means "looks
good but we haven't looked hard enough yet." Ship only when both are satisfied.

Compute both with:

```bash
python scripts/readiness.py            # runs gates + coverage, reads history
python scripts/readiness.py --no-gates # skip running pytest/ruff/mypy (use cached)
```

## Why not "perfect code, no review"?

Unachievable for non-trivial software: semantic correctness is undecidable in
general (Rice's theorem); the *spec itself* is incomplete and is discovered
through review (most zipmonkey defects were docstring/code contract gaps, not
logic errors); and behaviour depends on empirical facts about dependencies
(which exact exception a stdlib call raises). The review cycle is the
spec-discovery mechanism. So the goal is not "zero cycles" — it is a
**measurable, auditable release decision**, which is what these scores provide.

## Hard gates (any failure caps RRS at 40 = NOT releasable)

- all tests pass
- lint clean (`ruff`)
- type-check clean (`mypy`)
- zero **known-open** defects (anything not-fixed must live in `LIMITATIONS.md`
  as a *decision*, not an open defect)

## RRS components (weights sum to 100)

| Component | Weight | How it's measured |
|---|---|---|
| Test coverage | 15 | line+branch %, scaled against a 90% target |
| Property / round-trip tests present | 10 | Hypothesis round-trips exist |
| Contract coverage | 20 | fraction of public docstring promises with a pinning test (estimated in config until automated) |
| Convergence confidence | 25 | from the panel history (see below) |
| Static rigor | 15 | mypy clean + ruff ruleset |
| Docs | 10 | LIMITATIONS / SKILL / CONTRIBUTING / README present |
| Security posture | 5 | traversal/bomb/normalisation tests present |

## Convergence

Weight each panel's **new, confirmed** defects by severity
(`CRITICAL=40, HIGH=10, MEDIUM=4, LOW=1, NIT=0.2`).

- **Convergence Rate** `CRₙ = Wₙ / W₁` — this panel's weighted yield vs the
  first. Trends toward 0, but **not monotonically** — a deep HIGH can surface
  late (it did, in Panel C).
- **Clean streak** — consecutive trailing *full-diversity* panels with weighted
  yield below `tau` (default 2 ⇒ "nothing above LOW").
- **Convergence confidence** `= 1 − e^(−streak × diversity)`, where `diversity`
  is the fraction of available models that participated. `0` until a panel comes
  back effectively clean.
- **Convergence score** (feeds RRS) `= 0.5·decline + 0.5·confidence`, where
  `decline = 1 − min(1, CR_last)`.

## Release rule

Ship when **all gates green AND RRS ≥ 90 AND clean streak ≥ 2 at full
diversity**. The streak requirement is the real safeguard: it means two
independent, fully-briefed panels in a row found nothing above LOW.

## Maintaining the history

After each panel + fix, append the panel to `release_readiness.json` with its
participating `models` and its `findings` counts by severity (the *new,
confirmed, adjudicated* ones — not re-reports, not documented tradeoffs). Then
re-run `scripts/readiness.py`.

**Anti-gaming:** every input must be adversarially sourced — coverage from a
mutation-meaningful suite, convergence from *independent* models each given the
full testing philosophy (see `CONTRIBUTING.md`). A score computed from a weak
panel is worthless.
