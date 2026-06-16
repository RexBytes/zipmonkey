# zipmonkey

Smart archive inspection and extraction. Collapses the repetitive `zipfile` /
`tarfile` boilerplate into one clean API: auto-clean OS junk, inspect without
extracting, selective extraction, flattening, recursive unpacking of nested
archives, and magic-byte type detection — with a CLI on top.

Part of the `*monkey` toolkit. MIT licensed.

## Install

```bash
pip install zipmonkey
# optional backends:
pip install zipmonkey[sevenzip]   # .7z support (py7zr)
pip install zipmonkey[rar]        # .rar support (rarfile)
```

Core features need only the standard library. Requires Python 3.11+.

## Why

Users upload ZIPs full of nested ZIPs, mixed file types, Mac `__MACOSX`
garbage, and inconsistent structure. You end up writing the same `zipfile`
loop — strip junk, handle nesting, detect types, clean up temp dirs — every
time. zipmonkey is that loop, done once, opinionated.

## Quick start

```python
import zipmonkey

# Inspect without extracting.
report = zipmonkey.inspect("bundle.zip")
print(report.format, report.file_count, report.total_size)
for e in report.entries:
    print(e.name, e.detected_type, e.is_artifact)

# Extract with automatic temp-dir cleanup.
with zipmonkey.open("bundle.zip") as arc:
    result = arc.extract()                       # temp dir, cleaned on exit
    print(result.count, "files ->", result.dest)

# Selective + recursive extraction to a directory you own.
zipmonkey.extract("bundle.zip", "out", include="*.csv", recursive=True)

# Walk extracted files tagged for dispatch.
for tf in zipmonkey.walk_typed("bundle.zip", "out"):
    print(tf.path.name, tf.detected_type, tf.category)
```

### What it handles for you

- Strips `__MACOSX/`, `.DS_Store`, AppleDouble `._*`, `Thumbs.db`, `desktop.ini`.
- Detects format by **magic bytes**, not extension (a mislabelled archive still opens).
- Unpacks **nested** archives (zip-in-zip, tar.gz-in-zip) with depth and size caps.
- Skips **path-traversal** (`..`) members; re-roots absolute paths under the destination.
- **Flattens** to one directory, renaming basename collisions (`name (1).ext`).
- Filters with `include` / `exclude` globs (matched against full path and basename).

## Formats

ZIP, tar, tar.gz, tar.bz2, tar.xz, standalone gzip/bzip2/xz files, plus 7z and
rar when the optional extras are installed.

> **Streaming-safety note.** The decompression-bomb caps are enforced *while
> streaming* for the stdlib-backed formats (ZIP/tar/gzip/bzip2/xz) and rar. The
> optional **7z** backend materialises each member in memory before writing
> (py7zr has no streaming API); a declared-size preflight rejects oversized
> members before decompression, but peak memory for an honestly-declared large
> 7z member is proportional to its size. Apply a small `max_total_bytes` (or
> your own preflight) for untrusted large 7z archives. See `LIMITATIONS.md`.
>
> For untrusted input prefer `extract()` (streamed, capped) over `read()` /
> `open_member()`, which return whole members and do **not** apply the caps.

## CLI

```bash
zipmonkey inspect bundle.zip      # summary + per-file table
zipmonkey tree    bundle.zip      # indented content tree
zipmonkey extract bundle.zip out --include "*.csv" --recursive --flat
# safety caps and overwrite are flags (0 disables a cap):
zipmonkey extract big.zip out --max-total-bytes 0 --max-files 0 --max-depth 0 --no-overwrite
```

## Pairs well with

Extracted files usually need more processing — dispatch on `TypedFile.category`:

- `tabular` (CSV/TSV) -> [`dsvmonkey`](https://pypi.org/project/dsvmonkey/)
- `pdf` -> [`pdfmonkey`](https://pypi.org/project/pdfmonkey/)
- `excel` -> [`xldetect`](https://github.com/RexBytes/xldetect) + [`xlfilldown`](https://pypi.org/project/xlfilldown/)

## Using with AI assistants

This package ships a `SKILL.md` in its repo root with an LLM-oriented decision
tree, worked examples, and a "don't" list. See `LIMITATIONS.md` for deliberate
design tradeoffs (e.g. why tar archives show a `0.00` compression ratio).

## License

MIT — see `LICENSE`.
