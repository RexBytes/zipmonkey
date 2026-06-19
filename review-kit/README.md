# review-kit

A portable, project-agnostic kit for running the competitive multi-model review
methodology (and the release decision it feeds) on any Python package. Copy
these files into a new project and fill the placeholders — no project-specific
content travels with the kit.

Developed on `zipmonkey`; see that repo's `REVIEW_HISTORY.md` for a worked
example of the methodology over 11 panels.

## What's here

| File | Copy to | Edit? |
|---|---|---|
| `CONTRIBUTING.md` | repo root | as-is (generic methodology + lessons) |
| `RELEASE_READINESS.md` | repo root | as-is (the rubric) |
| `scripts/readiness.py` | `scripts/` | **as-is — byte-identical across projects** |
| `release_readiness.template.json` | `release_readiness.json` | fill `available_models`, `signals`, append panels |
| `LIMITATIONS.template.md` | `LIMITATIONS.md` | start empty; add entries as panels surface tradeoffs |
| `REVIEW_HISTORY.template.md` | `REVIEW_HISTORY.md` | fill as panels run |
| `ci.template.yml` | `.github/workflows/ci.yml` | replace `<package>`/extras/dep-matrix |
| `panel_prompt.template.md` | (keep as your panel brief) | fill `<…>` each round |

`readiness.py` reads every project-specific value from `release_readiness.json`
(`signals`, `available_models`, weights, targets), so the script never needs
editing — only the JSON differs per project.

## Setup

1. Copy the files to the targets above.
2. In `pyproject.toml` add a dev extra and pytest config (so the suite runs from
   a clean clone):
   ```toml
   [project.optional-dependencies]
   dev = ["pytest", "pytest-cov", "hypothesis", "ruff", "mypy"]
   [tool.pytest.ini_options]
   testpaths = ["tests"]
   pythonpath = ["src"]
   ```
3. `pip install -e ".[dev]"`, get the suite green, then `python scripts/readiness.py`.

## Running a panel

1. Fill `panel_prompt.template.md` (HEAD sha, baseline counts, where-to-look,
   the running already-fixed list).
2. Launch one reviewer per available model on the SAME brief; allow same-model
   sub-agents.
3. Adjudicate (see `CONTRIBUTING.md`): reproduce singletons before fixing;
   dismiss mock-only/documented with a reason.
4. Fix in batches with regression tests; append the panel to
   `release_readiness.json` and `REVIEW_HISTORY.md`; re-run `readiness.py`.

## Release gate

`readiness.py` reports RELEASABLE only when: all hard gates pass (tests/lint/
type/no-open-defects), RRS ≥ 90, AND ≥ 2 consecutive full-diversity clean
panels. Verify the gate can actually say *yes* for your model set (set
`available_models` to the models you actually have, or full diversity is
unreachable).

## Keeping it out of the project's tooling

This kit is inert to a project's checks when dropped into `review-kit/`:
`pytest` (`testpaths=["tests"]`), `ruff check src tests`, and `mypy` (`files=
["src"]`) are all scoped, so nothing here is collected, linted, or type-checked.
Keep the templates out of `tests/` and `src/` and they won't pollute the project.
