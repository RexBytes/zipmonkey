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
| Multi-model review panels | 12 (3 models each: opus, sonnet, haiku) |
| Confirmed findings (panels) | 22 — 0 CRITICAL, 1 HIGH, 6 MEDIUM, 10 LOW, 5 NIT |
| Severity-weighted yield | 15 → 8 → 5.2 → 1 → 6.4 → 5 → 1 → 0 → 1 → 1 → 1.2 → 0.2 |
| Tests | 278 passing / 1 skipped with py7zr+rarfile present; ruff + mypy clean; ~92% coverage. **CI green** (py7zr 1.1.0/1.1.3 matrix) |
| Release-Readiness Score | 95.8 / 100 |
| Convergence | clean streak ≥ 2 at full diversity ✓; confidence 1.00 |
| Verdict | **RELEASABLE** — gates green, CI green, RRS ≥ 90, Panel 12 full-diversity clean on the exact shipping tree |

> **Converged and RELEASABLE on the shipping tree (`a70af86`).** Panel 12 is a
> full-diversity clean panel directly on the code being shipped (all three models
> 0 above-NIT; one dismissed cosmetic NIT). The earlier "RELEASABLE" after Panel
> 8 was on the pre-consolidation tree; it then took a consolidation, a CI fix, and
> a four-panel fight with one ported feature to honestly re-earn it — see the
> sections below.

> **Converged.** Panels 7 and 8 are two consecutive full-diversity panels with
> nothing above LOW (Panel 8 found nothing at all from any model), so the
> clean-streak safeguard is satisfied; RRS is 94.3 and every hard gate is green.
> The arc tells the story: an early structural/security cluster (Panel 1),
> a steady decay, a near-clean Panel 4 that the gate correctly refused to ship,
> a non-monotonic resurgence (Panels 5–6 — silent data loss from one fragile 7z
> mechanism), a **root-cause refactor** that replaced the mechanism, and two
> clean panels confirming the surface converged. Residual risk is low and
> measured. Remaining known-unverified item: the rar *content* path (no `rar`
> binary in this environment) — it rests on CI and code review, not the panel
> signal; see the release-decision note below.

## Trajectory

Severity weights: CRITICAL=40, HIGH=10, MEDIUM=4, LOW=1, NIT=0.2.

| Panel | Findings | Weighted | Theme |
|---|---|---|---|
| 1 | 1 HIGH, 1 MEDIUM, 1 LOW | 15.0 | Optional-dependency API break + backend symmetry gaps |
| 2 | 2 MEDIUM | 8.0 | Error-normalisation gaps on the single-file backend (truncated streams; over-long names) |
| 3 | 1 MEDIUM, 1 LOW, 1 NIT | 5.2 | 7z symlink detection; uniform missing-member contract |
| 4 | 1 LOW | 1.0 | Encrypted-header 7z password mislabel (first clean panel) |
| 5 | 1 MEDIUM, 2 LOW, 2 NIT | 6.4 | 7z interior-`..` silent data loss; flat/recursive ENAMETOOLONG siblings; dir open_stream contract |
| 6 | 1 MEDIUM, 1 LOW | 5.0 | 7z `..`-prefixed basename loss (Panel-5 fix regression); overwrite=False collision bucket |
| — | _root-cause refactor_ | — | 7z read → in-memory BytesIOFactory keyed by member name (commit `d3cd07a`) |
| 7 | 1 LOW | 1.0 | Recursive-7z cap-before-filter (documented); duplicate-name read HIGH dismissed — refactor held |
| 8 | _none_ | 0.0 | Clean at full diversity (broad sweep off the 7z seam) — release gate satisfied (pre-consolidation) |
| 9 | 1 LOW | 1.0 | Confirmation post-consolidation+CI-fix: 7z extension-detected decoy leaf escaped the filter (fixed); CI red→green |
| 10 | 1 LOW | 1.0 | flat_used reservation leak in the decoy fix (consensus opus+sonnet; fixed) |
| 11 | 1 LOW, 1 NIT | 1.2 | 4th issue in the extension feature (cap-before-filter for archive-named) → feature REVERTED to peek/content-sniff |
| 12 | 1 NIT | 0.2 | Full-diversity clean on the reverted (shipping) tree — RELEASABLE |

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

- **6 — a fix that spawned a sibling bug (commit `7db1582`).** haiku found
  nothing.
  - **MEDIUM (opus, reproduced).** Panel 5's interior-`..` escape guard
    (`norm.startswith("..")`) was a raw string-prefix test, so it also swallowed
    legitimate 7z basenames that merely begin with two dots (`..notes.txt`,
    `...txt`, `..foo`), reading them as `b""` — a *new* silent-data-loss instance
    of the very class the guard fixed. Tightened to reject only a true leading
    `..` path component (`norm == ".."` or `norm.startswith(".." + os.sep)`).
  - **LOW (sonnet, reproduced).** With `overwrite=False`, a same-archive
    normalised duplicate (`a.txt` + `./a.txt`) was bucketed as `skipped_existing`
    instead of `skipped_collisions` because the overwrite/exists test ran before
    the same-session `written_targets` check. Data was always correct (first
    wins); reordered the two checks. A genuinely pre-existing file still lands in
    `skipped_existing`.

- **Root-cause refactor (between Panels 6 and 7, commit `d3cd07a`).** Rather than
  patch a *seventh* instance of the 7z path-reconstruction class, replaced the
  mechanism: `_SevenZipBackend.read` now reads each member in-memory via py7zr's
  `BytesIOFactory`, keyed by py7zr's own member name (py7zr 0.x keeps `read()`).
  There is no path to reconstruct, so the interior-`..` and dot-prefixed
  data-loss cases — and any future variant — are gone by construction.

- **7 — the refactor holds; first clean panel of the new streak (commit
  `ab4de25`).** opus attacked the new `BytesIOFactory` read path directly (the
  `factory.get` key-mismatch surface, normalization-duplicate names, the `limit`
  boundary, hostile externally-crafted names) and found **nothing** — strong
  evidence the mechanism change closed the seam. No code defects.
  - **LOW (sonnet, reproduced) → documented.** Under `recursive=True` the
    non-streaming 7z backend enforces `max_member_bytes` before the leaf filter
    (every member must be materialised to sniff for nesting; containers bypass
    leaf filters). Not cleanly fixable without breaking a documented invariant;
    recorded as an intentional cost-of-fix tradeoff in `LIMITATIONS.md`.
  - **Dismissed (haiku rated "HIGH").** Reading a duplicate member name returns
    one member. opus and sonnet both independently judged it correct; a by-name
    API cannot disambiguate two members sharing a name, and extraction preserves
    both (`skipped_collisions`). Documented as a fundamental-ambiguity limitation.
    Both behaviours pinned with golden-behaviour tests.

- **8 — converged; release gate satisfied (no code change).** All three models
  found **zero** defects across a broad assault deliberately steered *off* the
  now-closed 7z seam: `safe_target` traversal / realpath / drive-letter / UNC /
  control-char rejection, `detect.py` thresholds, cap enforcement at N vs N+1
  with partial-result cleanup, `is_os_artifact` four-corner tests, `models`
  invariants, CLI exit codes, and the single-file backends (sonnet alone ran
  ~35 real-input repros). The only thing raised was the long-standing rar
  directory-member asymmetry, again **read-only-unverified** (no `rar` binary) —
  not a confirmed finding. This is the **second consecutive full-diversity clean
  panel**, so the clean-streak safeguard is met and the package is RELEASABLE.

## Post-release consolidation (merge of the parallel `determined-darwin-hhhw8t` effort)

A separate, earlier branch (`claude/determined-darwin-hhhw8t`) ran its own
12-panel campaign over the same package. Its distinct, valuable work was merged
onto this branch:

- **Portable `review-kit/` directory** — the reusable kit (templates + a
  config-driven `readiness.py`) as a committed subtree; inert to project tooling.
- **`looks_like_archive()` + extension-based nested-archive detection for the
  non-streaming 7z backend.** This **resolves the Panel-7 "recursive 7z enforces
  `max_member_bytes` before the filter" limitation**: 7z no longer materialises a
  member just to sniff it, so the filter and caps now behave uniformly with the
  streaming backends. Tradeoff (documented): a nested 7z under a non-archive name
  is treated as a leaf.
- **Eager `dest is None` guard** in `zipmonkey.extract()` / `walk_typed()`
  (raises a clear `TypeError` instead of deleting an auto temp dir).
- **`safe_target` rejects DEL (0x7F)** alongside the C0 control range.

Each landed with a regression test; the suite is **277 passing / 1 skipped**,
ruff + mypy clean. *Deliberately kept ours* over hhhw8t's: the `is_special`
docstring (ours is current; hhhw8t's was stale) and the `file_count`/`total_size`
semantics (a design choice, not a defect). The `_SevenZipBackend.read`
BytesIOFactory refactor (this branch) was kept over hhhw8t's temp-dir approach.

> **Convergence caveat:** the clean streak below was earned by Panels 7–8 on the
> *pre-consolidation* tree. This consolidation changed converged code (notably
> the recursion path), so the streak is **stale for the current tree** — a
> confirmation panel (Panel 9) is recommended before treating the merged result
> as re-converged. Gates remain green and tests pass, but the panel signal needs
> to be refreshed on the merged code.

## CI was red for ~7 commits (caught a real cross-version 7z defect)

The branch CI had been **failing since Panel 3** — the `optional-backends` job's
**py7zr 0.20.8** matrix leg, on the 7z symlink test. py7zr 0.20.8 (and 1.0.x)
expose *no* symlink information on `FileInfo`; `FileInfo.is_symlink` only landed
in **1.1.0**, so 7z symlink skipping (a real safety feature) couldn't work on
the older pin. Local runs used py7zr 1.1.3 and were blind to it — and
`readiness.py`'s gates run *local* pytest only, so the "RELEASABLE" verdict had
been computed without ever exercising the version matrix. This is the
documented "CI is non-negotiable; it sees what local runs cannot" lesson biting
in practice. Fixed by bounding the dependency `py7zr>=1.1,<2`, matrixing CI over
`[1.1.0, 1.1.3]`, and dropping the now-dead py7zr 0.x read fallback. CI is green
(commit `11c2a76`).

## Panel 9 — confirmation on the merged + CI-fixed tree (commit `0ebc8f9`)

Full-diversity confirmation panel. **haiku and sonnet found zero defects.** opus
found **1 LOW** — a regression from the consolidation's extension-based 7z
nesting: a non-archive named like an archive (a text file `decoy.zip`) inside a
7z is *presumed* a container by `looks_like_archive()`, so it bypasses the leaf
filter, is written to be opened, fails to open as an archive, and was then
extracted as a leaf — escaping `include`/`exclude`. The content-sniffing ZIP/tar
backends filter it correctly. Fixed by re-applying the leaf filter when an
extension-classified container fails to open (and unwinding the file/byte budget
of the file written only for the open-attempt). Real nested 7z archives still
always traverse; the no-filter path is unchanged.

> **Open item before final ship:** the Panel-9 decoy fix (`0ebc8f9`) changed
> code *after* the panel reviewed it, so `readiness.py`'s RELEASABLE (streak 3,
> RRS 94.5) is not yet honest for the current HEAD. A **Panel 10** confirmation
> on the post-fix tree is needed to re-establish the clean streak on the exact
> code being shipped. (Two of three Panel-9 models were already clean and the
> fix is small/tested, so residual risk is low — but the methodology's whole
> point is to *measure* that, not assume it.)

## Panels 10–12 — the extension feature, fought and reverted

Panel 9's decoy fix introduced a sibling bug Panel 10 caught (the `flat_used`
leak, consensus opus+sonnet, fixed). Panel 11 then found a *fourth* issue in the
same ported feature (the extension-based 7z nested-archive detection): an
archive-named, oversized, filtered-out member tripped `max_member_bytes` before
the filter (opus LOW), plus an empty-dir NIT (sonnet). Three straight panels with
a defect in one feature is a design signal, not bad luck — so per a pre-agreed
guardrail the feature was **reverted** to the pre-consolidation content-sniff
(peek) recursion, eliminating all four extension defects at once and restoring
*more* correct behaviour (a real archive under a non-archive name is recursed
again). **Panel 12** then confirmed the reverted tree full-diversity clean
(0 above-NIT; one dismissed cosmetic trailing-dot NIT).

## Release decision (v1.0.0) — RELEASABLE

`python scripts/readiness.py` → **RELEASABLE**: all hard gates green (tests /
ruff / mypy / no open defects), **CI green** (py7zr 1.1.0/1.1.3 matrix),
**RRS 95.8 / 100**, **clean streak ≥ 2 at full diversity** (confidence 1.00).
Panel 12 is a full-diversity clean panel on the **exact shipping tree**
(`a70af86`); the reverted recursion is the Panels 1–8 code and every other
component was reviewed clean across Panels 9–12.

**What the convergence signal covers:** the full ZIP, tar, and gzip/bz2/xz
single-file surfaces, and the **7z** backend (py7zr installed and exercised for
real across twelve panels — the richest source of defects and now the most
heavily tested).

**What it does *not* cover (rests on CI + code review instead):** the **rar
content path**. This environment has no `rar`/`unrar` binary, so `.rar` fixtures
can't be created and rar read/extract can't be exercised end-to-end. The rar
*code* was read every panel; one directory-member `open_stream` asymmetry is
noted as known-unverified, to confirm and (if real) fix when a rar binary is
available. Ship-blocking only for callers who rely on the optional rar backend.

**Known cosmetic NITs (below the action threshold, not fixed):** a trailing-dot
member name (`file.`) yields a `file (1).` collision suffix in flat mode
(`_split_ext` vs `_extension` disagreement) — pathological input, no data loss.

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
- **A fix can spawn a sibling bug.** Panel 6's MEDIUM was a regression in Panel
  5's own fix (an over-broad `..` string-prefix guard). Re-review the next panel
  with the previous panel's *fixes* themselves in scope, not just the original
  code.
- **A recurring source is a design signal, not just bad luck.** The 7z backend
  yielded in five of six panels; the data-loss cases (Panels 5–6) all trace to
  one design choice — `_SevenZipBackend.read`'s extract-to-temp + path
  reconstruction (the py7zr ≥ 1.0 workaround). When instances keep coming from
  the same seam, weigh replacing the mechanism (e.g. an in-memory read keyed by
  py7zr's own member name) against patching each instance.
- **Know when to revert, not patch.** The extension-based 7z detection (ported
  from another branch) produced a fresh LOW in Panels 9, 10, and 11 — each a new
  edge case of "guess by name, then write/open/maybe-unwind." A pre-agreed
  guardrail ("if it recurs once more, revert") meant the 12th panel confirmed a
  *simpler, content-correct* recursion instead of a fourth patch. Set the
  ship-vs-continue (and patch-vs-revert) rule **before** you're emotionally
  invested in the fix.
- **CI gates ≠ local gates.** `readiness.py` runs *local* pytest; it was blind
  to the py7zr version matrix and reported RELEASABLE while CI was red for seven
  commits (py7zr < 1.1 lacks `FileInfo.is_symlink`). A release decision must
  state CI status explicitly, not infer it from a green local run.

_Maintenance: append a row to the trajectory table and a bullet per new panel;
keep the TL;DR numbers in sync with `release_readiness.json`._
