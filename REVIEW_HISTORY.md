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
| Multi-model review panels | 5 (3 models each: opus, sonnet, haiku) |
| Confirmed findings (panels) | 14 — 0 CRITICAL, 1 HIGH, 5 MEDIUM, 5 LOW, 3 NIT |
| Severity-weighted yield | 15.0 → 8.0 → 5.2 → 1.0 → 6.4 (non-monotonic) |
| Tests | 266 passing / 1 skipped with py7zr+rarfile present; ruff + mypy clean; ~91% coverage. Default no-extras suite: ~248 passing / 5 skipped |
| Release-Readiness Score | 78.2 / 100 |
| Convergence | clean streak 0 of 2 required (RESET by Panel 5); confidence 0.00; rate 0.43 |
| Verdict | NOT RELEASABLE — Panel 5 found a MEDIUM, resetting the clean streak |

> **Panel 5 is why the gate requires _two_ consecutive clean panels.** After
> Panel 4's near-clean (RRS had crossed 90, streak 1), the deciding panel —
> instructed to try hardest — surfaced a **silent data-loss MEDIUM** (7z
> interior-`..` members extracting as empty files) plus two `ENAMETOOLONG`
> crash LOWs that were siblings of a Panel-2 fix. The yield jumped 1.0 → 6.4
> (convergence is non-monotonic, exactly as documented), the streak reset to 0,
> and RRS fell back to 78.2. Had the rule been "one clean panel," we would have
> shipped silent data loss. All five findings are now fixed with regression
> tests; the gate needs two fresh consecutive clean panels from here.

## Trajectory

Severity weights: CRITICAL=40, HIGH=10, MEDIUM=4, LOW=1, NIT=0.2.

| Panel | Findings | Weighted | Theme |
|---|---|---|---|
| 1 | 1 HIGH, 1 MEDIUM, 1 LOW | 15.0 | Optional-dependency API break + backend symmetry gaps |
| 2 | 2 MEDIUM | 8.0 | Error-normalisation gaps on the single-file backend (truncated streams; over-long names) |
| 3 | 1 MEDIUM, 1 LOW, 1 NIT | 5.2 | 7z symlink detection; uniform missing-member contract |
| 4 | 1 LOW | 1.0 | Encrypted-header 7z password mislabel (first clean panel) |
| 5 | 1 MEDIUM, 2 LOW, 2 NIT | 6.4 | 7z interior-`..` silent data loss; flat/recursive ENAMETOOLONG siblings; dir open_stream contract |

## What each panel found and how it was fixed

- **1 — dependency break + backend symmetry gaps (commit `c3f5b5e`).**
  - **HIGH (consensus, all 3 models).** `_SevenZipBackend.read()` called
    `SevenZipFile.read()`, which **py7zr ≥ 1.0 removed**, so every 7z content
    path (`read`/`peek`/`open_member`/`extract`/`inspect`) raised against the
    version the unbounded `py7zr>=0.20` constraint resolves to (1.1.3). The
    default suite was *blind* to it — py7zr was absent, so the 7z tests skipped
    (the "blind spots" lesson, proven live). Fixed by switching to the
    `extract(targets=…)` API that is stable across py7zr 0.x and 1.x (verified
    on 0.20.8 and 1.1.3), bounding the dep to `py7zr>=0.20,<2`, and adding a
    py7zr 0.x/1.x CI matrix so the boundary is exercised rather than skipped.
  - **MEDIUM (singleton, sonnet — reproduced before fixing).** ZIP symlink
    members read as their *link-target bytes* instead of `b""`, violating the
    documented "special members read as empty" contract that the tar backend
    already honoured. Fixed with the symmetric `is_special` guard in
    `_ZipBackend.read`/`peek`/`open_stream`.
  - **LOW (sonnet; opus also noted).** `_human_size` carry guard gave up at the
    last unit, emitting `"1024.0P"` for ≥ 1 EiB inputs. Fixed by extending the
    unit ladder through E/Z/Y.
  - Each fix landed with a regression test pinning the restored contract.

- **2 — error-normalisation gaps on the single-file backend (commit `4ac5217`).**
  - **MEDIUM (consensus, opus + sonnet — reproduced).** A truncated/corrupt
    gzip or xz stream passes `validate()` (one byte decodes) but then raised a
    raw `EOFError`/`LZMAError` from `_SingleFileBackend._streamed_size()` when
    `entries()`/`inspect()`/`namelist()` stream the payload to size it —
    escaping the documented exception hierarchy and crashing the CLI with an
    uncaught traceback. The single-file backend was the lone cell where
    `entries()` itself decompresses (ZIP/tar read only metadata), so the read
    guard was bypassed — another symmetry gap. Fixed by wrapping the loop in
    `_DECOMP_ERRORS` and re-raising as `ArchiveReadError`.
  - **MEDIUM (singleton, haiku — reproduced; right-sized from its "HIGH").** A
    member whose basename exceeds the filesystem `NAME_MAX` raised an uncaught
    `OSError(ENAMETOOLONG)` from `_prepare_target`, aborting the whole
    extraction and discarding the result — while every other unplaceable member
    (unsafe path, collision, special) is recorded and skipped. Fixed by catching
    `ENAMETOOLONG` (now probed first, so the `overwrite=False` existence check
    can't trip it either) and recording the member in `skipped_unsafe`.
  - **Dismissed with reason:** haiku's "CRITICAL" — `_SingleFileBackend.read()`
    ignoring the member name — is harmless single-member leniency, not a
    security boundary (a single-file archive has exactly one member); sonnet
    independently judged the same area below threshold. The `open_stream`
    return-type asymmetry NIT is cosmetic and already handled by the caller.

- **3 — 7z symlink detection + a uniform missing-member contract (commit
  `b49aaeb`).**
  - **MEDIUM (singleton, sonnet — reproduced).** The 7z backend hardcoded
    `is_special=False`, so symlink members (py7zr exposes `FileInfo.is_symlink`)
    were never flagged — they extracted as empty regular files, inflated
    `ExtractResult.count`, never landed in `skipped_links`, and made `inspect()`
    raise on an escaping symlink. Every other backend (zip/tar/rar) detects
    symlinks; 7z was the empty cell. Fixed to flag, read-as-empty, and skip them.
    No host-file disclosure existed (py7zr blocks escaping symlinks), so MEDIUM.
  - **LOW (opus rated NIT, haiku rated MEDIUM; converged across panels).**
    Reading a member name not in the archive was inconsistent — zip/tar leaked a
    raw `KeyError`, 7z silently returned `b""`, and the single-file backend
    returned the lone member's payload for *any* name. Normalised to one
    contract: a missing member raises `ArchiveReadError` on every backend, now
    documented. This supersedes Panel 2's "harmless single-member leniency"
    dismissal — once the contract is made uniform and written down, the leniency
    became the outlier worth removing (lesson: make contracts explicit so the
    same gap stops being re-litigated each panel).
  - **NIT (opus).** 7z solid-block members report `compressed_size=0` — the same
    class as the documented tar limitation. Extended that `LIMITATIONS.md` entry
    to cover 7z solid blocks rather than inventing a per-member number.

- **4 — encrypted-header 7z password message; first clean panel (commit
  `d996e8c`).** sonnet and haiku found **zero** defects; opus found one **LOW**:
  a 7z written with an *encrypted header* needs the password just to list
  members, so a missing password was folded into the generic "corrupt or
  unsupported 7z archive" message — indistinguishable from a genuinely bad file
  — and surfaced at open time. Fixed to raise `UnsupportedArchiveError` naming
  the password cause, with the `open()` docstring amended for the carve-out. A
  *wrong* password yields a garbage-parse error genuinely indistinguishable from
  corruption, so it keeps the generic message (documented in code). Opus and
  sonnet also raised **read-only-unverified** rar observations (a directory
  `open_stream` asymmetry; a shared-filename special-set guard) — not
  reproducible without the `rar` binary, so per the rules of evidence they were
  not acted on; recorded here as known-unverified to revisit if the rar content
  path becomes testable.

- **5 — the gate earns its keep (commit `7a17097`).** The deciding panel, told to
  try hardest, broke Panel 4's apparent convergence.
  - **MEDIUM (opus, reproduced).** A 7z member stored with an interior `..`
    (`docs/../report.txt`) extracted as an **empty file** — silent data loss.
    py7zr writes the *normalised* path (`report.txt`) while `_SevenZipBackend.read`
    reconstructed the raw name and found nothing, returning `b""`; `safe_target`
    re-roots it in-bounds so it wasn't even flagged unsafe. Fixed with
    `os.path.normpath` (plus an escape guard). This was a latent flaw in the
    Panel-1 extract-to-temp workaround.
  - **LOW ×2 (sonnet, reproduced).** The `ENAMETOOLONG` class Panel 2 fixed for
    the non-flat path still crashed its **flat** (`_unique_basename`) and
    **recursive** (`_unique_dir`) siblings — the literal "flat-mode over-long-name
    crash that hid behind the non-flat one" this project's methodology cites.
    Now recorded in `skipped_unsafe` / `skipped_nested`.
  - **NIT ×2 (haiku, reproduced).** ZIP and 7z `open_stream` returned a stream
    instead of `None` for directory members, violating the `_Backend` interface
    (no functional impact). Fixed for contract conformance and symmetry.

## Standing themes

- **Blind spots are real and expensive.** The HIGH was invisible to a green
  local run because the optional 7z backend's tests skip when py7zr is absent. A
  panel only "counts" where it can see — install optional deps and let CI matrix
  their versions (now done for py7zr).
- **The late-stage defect class is symmetry gaps.** The MEDIUM was a guard
  present in one sibling (tar) but missing in its twin (ZIP). Audit behaviour
  path-by-path: read/open_member across every backend, special vs regular.
- **Pin and bound dependencies.** An open-ended `>=` eventually pulls a version
  that removed an API you call (lesson 7, manifested here exactly).
- **Honest bookkeeping.** A broken sandbox `rarfile`/cryptography import (pyo3
  panic) was correctly excluded as an environment artifact, not a zipmonkey bug.

_Maintenance: append a row to the trajectory table and a bullet per new panel;
keep the TL;DR numbers in sync with `release_readiness.json`._
