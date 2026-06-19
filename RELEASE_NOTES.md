# zipmonkey 1.0.0 — Hardened for Hostile Archives

**Release title:** `zipmonkey 1.0.0 — Hardened for Hostile Archives`

zipmonkey 1.0.0 is the first **stable** release. The API and behaviour are now
locked in and validated through an eight-round competitive multi-model review
campaign that found and fixed 17 real defects — including several silent
data-loss bugs in the optional 7z backend — and drove the package to a measured,
auditable release decision (`RELEASE_READINESS.md` → **RELEASABLE**, RRS
94.3/100).

If you extract untrusted or messy archives — nested ZIPs, Mac `__MACOSX` junk,
mixed file types, `.tar.gz`/`.7z`/`.rar` — this is the boilerplate loop done once,
safely.

---

## Highlights

- **One clean API** over `zipfile`/`tarfile` (+ optional `py7zr`/`rarfile`):
  `open` / `inspect` / `extract` / `walk_typed` / `detect_type`.
- **Inspect without extracting** — formats, per-member type, sizes, artifact
  flags, compression ratio.
- **Safe extraction of untrusted archives** — streamed decompression-bomb caps
  (`max_total_bytes`, `max_files`, `max_depth`, `max_member_bytes`),
  path-traversal (`..`) rejection, absolute-path re-rooting, symlink/device
  skipping, and `realpath`-resolved destination prefixes.
- **Magic-byte format detection** — a mislabelled archive still opens; extension
  fallback for ambiguous document/text types.
- **OS-junk cleanup** — `__MACOSX/`, `.DS_Store`, AppleDouble `._*`, `Thumbs.db`,
  `desktop.ini`.
- **Recursive unpacking** of nested archives, `include`/`exclude` glob filtering,
  and **flatten** mode with collision-safe renaming.
- **CLI** — `zipmonkey inspect | tree | extract`.
- **Formats** — ZIP, tar, tar.gz/bz2/xz, standalone gzip/bzip2/xz, plus 7z and
  rar via optional extras.

Core features need only the standard library. **Requires Python 3.11+.**

---

## Why 1.0.0 — the quality story

This release was hardened with the repository's competitive **multi-model review
kit** (`CONTRIBUTING.md`, `RELEASE_READINESS.md`, `scripts/readiness.py`): each
round ran three independent reviewers on *different* models (Opus, Sonnet, Haiku)
against the same code, with every finding reproduced from a real input before any
fix landed.

- **8 review panels**, **17 confirmed defects fixed** (1 HIGH, 6 MEDIUM, 7 LOW,
  3 NIT) — each with a regression test.
- **Root-cause refactor** of the 7z read path (in-memory read keyed by the
  member name) that eliminated an entire recurring silent-data-loss class instead
  of patching instances.
- **273 tests** passing (1 skipped — a `.rar` fixture needing the proprietary
  `rar` binary); **~91% coverage**; `ruff` + `mypy` clean.
- **Two consecutive full-diversity clean panels** and **RRS 94.3/100** satisfy
  the release gate. The full panel-by-panel record is in `REVIEW_HISTORY.md`.

A non-obvious save: a near-clean panel midway through *looked* shippable, but the
"two consecutive clean panels" rule held the line — the very next panel found
**silent data loss**. The gate worked.

---

## Notable fixes since 0.1.0

**Correctness / data integrity**
- 7z members with interior `..` or dot-prefixed names (e.g. `docs/../report.txt`,
  `..notes.txt`) no longer extract as empty files (**silent data loss**, now
  fixed by the read-path refactor).
- The optional 7z backend works against **py7zr ≥ 1.0** (which removed
  `SevenZipFile.read()`); the dependency is bounded and CI matrices py7zr 0.x/1.x.

**Robustness**
- Truncated/corrupt gzip/xz streams raise the documented `ArchiveReadError`
  instead of leaking a raw `EOFError`/`LZMAError` (and no longer crash the CLI).
- Member names exceeding the filesystem `NAME_MAX` are recorded and skipped
  (across flat, non-flat, and recursive modes) instead of aborting extraction
  with a raw `OSError`.
- Missing-member reads raise a uniform `ArchiveReadError` across every backend.

**Contracts & symmetry**
- ZIP and 7z symlink members are detected, read as empty, and skipped — matching
  tar.
- Encrypted-header 7z archives report a clear "password required" error rather
  than a generic "corrupt or unsupported" message.
- `_human_size` formats sizes correctly past the petabyte boundary.

See `git log` and `REVIEW_HISTORY.md` for the complete list.

---

## Install

```bash
pip install zipmonkey
# optional backends:
pip install "zipmonkey[sevenzip]"   # .7z  (py7zr)
pip install "zipmonkey[rar]"        # .rar (rarfile + system unrar/bsdtar)
```

---

## Known limitations & caveats

- **For untrusted input use `extract()`** — it streams under the caps. `read()`
  and `open_member()` return whole members and do **not** apply the caps.
- **Optional 7z** materialises each member in memory (py7zr has no streaming
  API), guarded by a declared-size preflight. Apply a small `max_total_bytes`
  for untrusted large 7z.
- **rar content is verified by code review + CI, not by the panel signal** — the
  review environment had no `rar`/`unrar` binary, so `.rar` read/extract could
  not be exercised end-to-end. One directory-member behaviour is noted as
  known-unverified, to confirm when a binary is available.
- **Not race-proof against a hostile *filesystem*.** Path checks assume `dest`
  is a private directory only you write to.

All deliberate design tradeoffs are documented in `LIMITATIONS.md`.

---

## Links

- Methodology & testing contract: `CONTRIBUTING.md`
- Release rubric & convergence metric: `RELEASE_READINESS.md`
- Full review trajectory (8 panels): `REVIEW_HISTORY.md`
- Deliberate tradeoffs: `LIMITATIONS.md`
