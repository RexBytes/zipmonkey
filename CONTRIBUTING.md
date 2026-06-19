# Contributing to zipmonkey

This document records **how zipmonkey is tested and reviewed**. It is the
project's quality contract: changes are expected to follow the testing
philosophy below, and significant changes are expected to survive the review
approach below.

Run the suite before sending anything:

```bash
pip install -e ".[dev]"
pytest -q          # tests
ruff check src tests
mypy               # type-check (config in pyproject.toml)
```

---

## Testing philosophy

Write tests that would **fail if a promise were broken**, not tests that
confirm the code you just wrote happened to run.

1. **Contracts are promises.** Every docstring sentence, type signature,
   parameter description, named behaviour, and threshold constant is a promise.
   Before testing a function, list its promises; write one test per promise.
   If you can't, the docstring is aspirational or wrong — fix the docstring.

2. **Boolean functions: all four truth-table corners.** For a predicate, cover
   confirmed-true, confirmed-false, false-for-a-*different*-reason, and
   true-under-adversarial-input. Example from this codebase: `is_os_artifact`
   is tested on junk (true), normal files (false), a name that merely *contains*
   `.DS_Store` (false for a different reason), and a bare `._` (adversarial
   true).

3. **Every parameter: empty, boundary, and the messy real input.** Probe the
   empty case, the boundary case, and the messy real-world input that motivated
   the code (the `__MACOSX`/nested-zip/bad-CRC archives that exist *because*
   users hit them).

4. **Pin thresholds just above and just below.** A "limit of N" means nothing
   unless `N` passes and `N+1` fails. See `max_files=4` with exactly one
   collision, the 262-byte tar-magic minimum, and `max_depth=1` allowing one
   nesting level but recording the next in `skipped_nested`.

5. **Fallbacks must preserve intent, not just type-check.** Ask what a fallback
   *returns* and whether the caller can still tell things apart: "missing" must
   stay distinguishable from "empty"; a re-rooted path must stay inside `dest`;
   a normalised exception must still carry the cause. A value that type-checks
   is not the same as a value that preserves meaning.

6. **Reach for stdlib primitives and their exact exception types.** Many bugs
   in this project hid in *which exception a library raises* and whether the
   catching code lists it (`zipfile.BadZipFile` subclasses `Exception`, not
   `RuntimeError`; `gzip.BadGzipFile` subclasses `OSError`). Prefer
   `codecs`/`zipfile`/`tarfile`/`zlib`/`lzma` semantics over assumptions.

7. **Round-trip property tests for transform/serialise paths.** Build an
   archive from arbitrary `{name: bytes}`, extract, and assert the bytes/file
   set/structure come back exactly (`tests/test_properties.py`, Hypothesis).
   Isolate the property from unrelated detection logic with explicit config
   (e.g. `clean_artifacts=False`, an `assume(...)` that excludes the unrelated
   collision class) so a falsifying example points at the right failure.

8. **Golden-file tests for CLI/formatted output.** Pin byte-exact stdout with
   volatile fields masked, so formatting drift or a wrong exit code fails
   loudly instead of silently breaking downstream scripts.

9. **When a test reveals a real source bug, keep the test.** Pin the current
   contract; mark `xfail(strict=True)` if needed and report the bug — don't
   quietly fix source in the same pass that changed the test, or you lose the
   regression signal.

Test layout mirrors source (`tests/test_<module>.py`) plus cross-cutting files:
`test_properties.py` (property/round-trip), `test_cli.py` (golden), and
`test_optional_backends.py` (7z/rar, gated by `importorskip`). Tests must not
modify source.

---

## Review approach: competitive multi-model panel

zipmonkey is reviewed by **spinning up several review agents on *different*
underlying models, pointed at the same code**, then adjudicating their reports.
This is how the bugs that survived single-reviewer cycles were finally found.

**Why it works.** Different models have different blind spots. Run head-to-head
on identical scope, their *overlap* is a high-confidence signal and their
*singletons* are leads to verify. On code that had passed nine sequential
review cycles, a four-run panel surfaced fourteen genuine, reproduced defects.

**How to run it.**

1. **Identical brief, different models.** Give each reviewer the *same* scope
   and the *same* brief, varying only the model. The brief **must include the
   full testing philosophy above, verbatim** — not a condensed summary. Every
   model is entitled to the same standard; do not hand one a fuller version than
   another, and do not assume a model "already knows it." (In the run that
   converged this package, the only reviewer given the complete philosophy
   caught the subtlest contract gaps the condensed-brief reviewers missed —
   that asymmetry is a methodology bug, not a model difference.) Also tell each
   to read `LIMITATIONS.md` first and not re-flag documented tradeoffs, to prove
   findings with a `/tmp` repro, and that false findings count against them.

   **Sub-agents stay on the same model.** A reviewer may spawn its own helper
   sub-agents (e.g. one per module) if it wants to parallelise — but every
   sub-agent it spawns must run on the *same* model as that panel slot, so each
   slot remains a clean single-model signal. Cross-model mixing happens only at
   the panel level (one slot per model), never inside a slot.

2. **Coordinator adjudicates; never relay unverified.**
   - **Consensus** (flagged by ≥2 models) → high confidence; fix.
   - **Singleton** → reproduce it yourself before touching code.
   - **False / no-op** → dismiss explicitly with the reason (e.g. an immutable
     cache wrongly called "stale"), don't churn.
   - Beware reviewers running a **stale checkout** — reconcile their line
     numbers and test counts against current `HEAD`.

3. **Fix in adjudicated batches with full-philosophy regression tests.** Every
   fix lands with a test that pins the contract it restores (four corners,
   just-above/below boundary, no-partial-left, intent-preserving fallback).

4. **The done signal.** A panel that comes back with only confirmations of
   prior fixes (no new real defects) — and reviewers that *cite* `LIMITATIONS.md`
   when triaging — is the signal the surface has converged.

### Watching the long tail

What these late panels are surfacing is **residual** — the leftover after every
prior cycle removed what it could. Two senses, both in play:

- **Residual defect** — a bug that *survived* earlier passes (not newly
  introduced); often hidden behind a more obvious sibling that got fixed first
  (e.g. the flat-mode over-long-name crash that hid behind the non-flat one).
- **Residual risk** — the estimated probability of *still-undiscovered* defects.
  It never reaches zero (you can't prove absence), so you measure it and decide
  if it's low enough to ship. The convergence metric is our proxy for it: a low,
  decaying per-panel yield ⇒ low residual risk. The goal is not residual = 0
  (unattainable) but residual *low enough, and measured*, to justify release.

Late panels behave differently from early ones; expect it rather than over-react:

- **The yield plateaus at a floor, not zero.** After the first few panels the
  severity-weighted yield drops sharply (here: 158 → ~14 → ~5) and then *sits*
  near a small floor. That floor is the long tail: one model finds one narrow
  thing per panel.
- **Findings get shallow, idiosyncratic, often pre-existing.** Long-tail defects
  are niche edges (a 300-char member name) or order bugs predating a recent
  refactor — not the structural/security clusters early panels surface. They are
  singletons (one model, not consensus), so reproduce each against a real input
  before fixing and dismiss mock-only ones.
- **Don't let the tail inflate severity.** A panel that finds only a niche LOW
  is *near-converged*, not "still broken." Keep the severity weights honest so
  the convergence metric reflects this.
- **Decide ship-vs-continue explicitly.** Each long-tail panel costs real work
  for shrinking returns. When successive panels return only niche LOW/NIT (or
  clean), weigh one more cycle against shipping with an explicit
  "RRS = X, known long-tail" note. Two consecutive full-diversity clean panels
  is the ideal stop; shipping a documented long tail is a legitimate alternative
  for a non-safety-critical library.

See `LIMITATIONS.md` for the deliberate tradeoffs reviewers should not re-litigate.

### Mind the blind spots (a clean panel only counts where it can see)

A panel can only "converge" on code it actually exercises. zipmonkey's optional
backends (7z via py7zr, rar via rarfile) are **gated by `importorskip`**, so
without those deps installed the 7z/rar tests *skip* — a panel run that way is
**blind** to them, and "clean" means nothing for that surface. Panel #9 proved
this: a reviewer who actually `pip install`ed py7zr found a HIGH (py7zr 1.0
removed `SevenZipFile.read()`, breaking the whole 7z feature) that every prior
panel and the default suite could not see.

Rules that follow:

- Before trusting convergence, list the code paths the panel could *not* run
  (optional deps, platform-specific branches) and exercise them explicitly.
- Pin dependency ranges and test across the boundaries (e.g. py7zr 0.x **and**
  1.x) — an unconstrained dep can silently break a feature with zero red tests.
  CI's `optional-backends` job runs the suite with the extras installed across a
  py7zr version matrix for exactly this reason.
- A release decision must state which surfaces the convergence signal covers and
  which rest on CI/other evidence instead.

## Lessons learned (reusable across packages)

Distilled from ten review panels on this package. These are deliberately
package-agnostic — apply them to any library, not just zipmonkey.

1. **Multi-model panels beat any single reviewer.** Different models have
   different blind spots. Run several on the *same* code; a finding flagged by
   two or more is high-confidence, a singleton is a lead to verify. Bugs that
   survived many single-reviewer cycles fell to the first diverse panel.
2. **Give every reviewer the full testing philosophy, verbatim.** Not a
   summary, not "it already knows." The one reviewer given the complete
   philosophy caught the subtlest contract gaps the condensed-brief ones missed;
   that asymmetry was a process bug, not a model difference.
3. **Demand real-input evidence; reject mock-only findings.** A defect that only
   reproduces by monkeypatching internals usually isn't reachable in practice.
   Require an executed repro built from real inputs, and reproduce it yourself
   before changing code.
4. **Convergence is non-monotonic and never reaches zero.** Yield drops fast
   then plateaus at a floor; a deep defect can still surface late (a HIGH
   appeared at panel 9). Measure *residual risk* (severity-weighted yield, a
   clean-streak, a confidence number) rather than chasing a "no bugs" proof.
5. **The late-stage defect class is symmetry gaps.** Once the structural bugs
   are gone, what remains is "a guard/behaviour present in one path but not its
   siblings" — flat vs non-flat, leaf vs nested, one backend vs the others, a
   method vs its convenience wrapper. Audit by building a path×behaviour matrix
   and finding the empty cell.
6. **CI is non-negotiable — it sees what local runs and reviewers cannot.**
   Gated/optional code paths (extras, platform branches) *skip* locally, so a
   green local suite and even a "clean" panel can be blind to them. A CI is
   required to: run tests + lint + type-check on every push; install the
   optional extras in a dedicated job so their tests actually execute; and
   **matrix the dependency versions** (an unconstrained dep silently broke an
   entire feature here with zero red tests until CI pinned 0.x vs 1.x). Treat
   "works on my machine with the happy-path deps" as unverified.
7. **Pin and bound dependencies, and test the boundaries.** An open-ended
   `>=` range will eventually pull a version that removed an API you call.
8. **Make the release decision explicit and measurable.** A rubric (gates +
   weighted score) plus a stop rule (e.g. two consecutive full-diversity clean
   panels) beats vibes. State which surfaces the signal covers and which rest on
   CI.
9. **Write down deliberate tradeoffs (a LIMITATIONS file).** It stops reviewers
   re-litigating settled decisions and stops agents "fixing" intended behaviour.
   The signal it works: a later reviewer *cites* it when triaging.
10. **Be honest in the bookkeeping.** Dismiss false positives with a stated
    reason; mark provisional vs final; keep the tree committed and the branch
    HEAD verified (shared environments can install broken deps or reset the
    checkout — confirm `git rev-parse HEAD` and restore the baseline after).

## Replicating this in another package

This approach is portable. To run it elsewhere, set up the same small set of
files — they are what make the methodology repeatable and auditable:

- **`CONTRIBUTING.md`** (this file) — the testing philosophy and the review-panel
  process. Start here; the rest support it.
- **`LIMITATIONS.md`** — a committed snapshot of *intentional* design tradeoffs
  that a reviewer might mistake for bugs (four fields per entry: concern /
  decision / rationale / escape hatch). Without it, every panel re-litigates the
  same decisions and agents "fix" deliberate behaviour. Reviewers must read it
  first and not re-report what it covers.
- **`RELEASE_READINESS.md`** + `release_readiness.json` + `scripts/readiness.py`
  — the release rubric (hard gates + weighted score) and the convergence metric
  (severity-weighted per-panel yield, clean streak, confidence), with a script
  that computes a number. This turns "feels done" into an auditable decision and,
  crucially, a gate that can actually say *yes* (verify the metric isn't
  unsatisfiable — see the diversity fix in this project's history).
- **`REVIEW_HISTORY.md`** — the narrative record: a TL;DR of the numbers, the
  panel-by-panel trajectory, and what each found/fixed. It shows whether the
  effort is converging and lets a newcomer see the whole arc at a glance.
- **CI** (`.github/workflows/…`) — non-negotiable (see lesson 6): run
  tests/lint/type on every push, install optional extras in a dedicated job, and
  matrix the dependency versions.

Minimum to start: `CONTRIBUTING.md` + `LIMITATIONS.md` + CI. Add the readiness
rubric and history file once panels begin, so the convergence signal is recorded
from the first round.
