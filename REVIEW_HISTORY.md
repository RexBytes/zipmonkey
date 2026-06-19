# Review History

A record of how this package is reviewed and hardened: the trajectory, the
issues found, and the fixes made. Methodology lives in `CONTRIBUTING.md`;
deliberate tradeoffs in `LIMITATIONS.md`; the release rubric in
`RELEASE_READINESS.md`.

This file is the narrative companion to `release_readiness.json`. Keep the two
in sync: every panel appended to the JSON gets a row in the trajectory table and
a bullet below, and the TL;DR numbers are re-derived from
`python scripts/readiness.py`.

## TL;DR

| Metric | Value |
|---|---|
| Multi-model review panels | 0 recorded under this kit (target 3 models each: opus, sonnet, haiku) |
| Confirmed findings (panels) | none recorded yet |
| Severity-weighted yield | — (populated once the first panel runs) |
| Tests | 240 passing, 5 skipped; ruff + mypy clean; 89% coverage |
| Release-Readiness Score | run `python scripts/readiness.py` |
| Convergence | clean streak 0 of 2 required; confidence 0.00 |
| Verdict | NOT RELEASABLE — no full-diversity clean panels recorded yet |

> The hard gates (tests, lint, type-check, no open defects) are green, but the
> release rule also requires RRS ≥ 90 **and** two consecutive full-diversity
> clean panels. No panels have been recorded against this kit yet, so the
> convergence signal is empty and the verdict is NOT RELEASABLE by design — not
> because of a known defect. Run panels (see `CONTRIBUTING.md`) and append them
> to `release_readiness.json` to build the streak.

## Trajectory

Severity weights: CRITICAL=40, HIGH=10, MEDIUM=4, LOW=1, NIT=0.2.

| Panel | Findings | Weighted | Theme |
|---|---|---|---|
| _none recorded yet_ | | | |

## What each panel found and how it was fixed

_No panels have been recorded under the review kit yet._ Append one bullet per
panel as panels run, e.g.:

- **A — <theme>.** <issues + fixes, one or two lines.>

## Standing themes

_To be filled as panels accumulate._ Expect (per `CONTRIBUTING.md`): convergence
is non-monotonic and never reaches zero; the late-stage defect class is symmetry
gaps (a guard present in one path but not its siblings); and gated/optional
backends (7z via py7zr, rar via rarfile) are invisible to a default run — CI
exercises them, so a "clean" local panel only counts where it can see.

_Maintenance: append a row to the trajectory table and a bullet per new panel;
keep the TL;DR numbers in sync with `release_readiness.json`._
