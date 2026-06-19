# Panel prompt template

The brief sent to each model slot in a review panel. Send the SAME text to every
model (vary only the model itself), filling the `<…>` placeholders. Run the
panel as: one slot per available model (e.g. opus, sonnet, haiku), each
optionally spawning SAME-model sub-agents. Then adjudicate (see CONTRIBUTING.md):
consensus → fix; singleton → reproduce yourself before fixing; mock-only/
documented → dismiss with a reason.

---

HEAD-TO-HEAD adversarial review (read-only; MAKE NO CHANGES) of `<package>` at
`<repo path>`. <N cycles / panels> deep. **"No new defects" after genuine effort
is the expected, valued result — do NOT manufacture findings; mock-only /
shallow / duplicate findings count against you.**

=== VERIFY LATEST CODE ===
`git -C <repo path> rev-parse HEAD` must equal `<HEAD sha>`. If your Read tool
disagrees with disk, distrust the cache and re-read via `python -c`/`nl -ba`.
Cite real current file:line. Already `pip install -e .[dev]`'d; pytest/ruff/mypy
pass (<X passed, Y skipped>). You MAY spawn helper sub-agents but they MUST run
on the SAME model as you. Run python/pytest/ruff/mypy + /tmp repros freely.

=== RULES OF EVIDENCE (strict) ===
A finding MUST reproduce with a REAL input (no monkeypatching/mocking internal
methods — those paths are intentionally not defended). Provide an executed repro
for every CONFIRMED finding. No real-input repro -> not a finding.

=== FULL TESTING / REVIEW PHILOSOPHY (apply rigorously; this is the standard) ===
Hunt inputs that FALSIFY a promise, not confirm the happy path.
1. Every docstring sentence, type, parameter description, named behaviour, and
   threshold constant is a promise — list them and break each. If docs claim X,
   find the input where the code does not-X.
2. Boolean functions: all four corners — confirmed-true, confirmed-false,
   false-for-a-DIFFERENT-reason, true-under-adversarial-input.
3. Every parameter: empty, boundary, and the messy real-world input it exists for.
4. Pin thresholds N (passes) and N+1 (fails).
5. Fallbacks preserve intent, not just type-check ("missing" vs "empty"; a
   normalised exception still carrying its cause).
6. Reach for stdlib primitives and their EXACT exception types — bugs hide in
   which exception a library raises and whether the catching code lists it.
7. Round-trip property thinking for transform/serialise paths: build -> run ->
   compare reproduces bytes/structure exactly.
8. Golden/CLI thinking: would formatting drift or a wrong exit code slip silently?
9. A test that reveals a real source bug IS the finding.

=== WHERE TO LOOK (tune per round) ===
<the deepest residuals for this package: optional/gated code paths and their
deps; interaction combinations not yet jointly tested; numeric/accounting
invariants; symmetry across siblings (a guard present in one path but not its
twins — variant A vs B, one backend vs others, a method vs its wrapper).>

=== ALREADY-FIXED (do NOT re-report) ===
<running list of fixes from prior panels, so reviewers hunt only what's left.>

READ `<repo>/LIMITATIONS.md` FIRST; do NOT re-report documented tradeoffs (you
may argue one is wrong, with reasoning).

=== DELIVERABLE ===
Numbered, severity-ranked (CRITICAL/HIGH/MEDIUM/LOW/NIT). Each: title; severity;
exact file.py:line; concrete real-input failure (input + what breaks); fix
direction. Mark CONFIRMED + the repro for executed items. NEW real defects only.
"No new defects" is valid and expected.
