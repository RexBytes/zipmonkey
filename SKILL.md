---
name: zipmonkey
description: >
  Use when working with archives: unzip, extract zip/tar/tar.gz/tar.bz2/7z/rar,
  inspect or list archive contents without extracting, strip __MACOSX and
  .DS_Store junk, unpack nested archives (zip in zip), flatten an archive to one
  directory, selectively extract by glob (only *.csv), detect file types by
  magic bytes, or walk extracted files tagged for dispatch. Phrases: "extract
  this zip", "what's in this archive", "unzip recursively", "remove mac
  junk", "nested zip", "archive tree".
---

# zipmonkey

Smart archive inspection and extraction. Replaces hand-rolled `zipfile` /
`tarfile` boilerplate. Stdlib-only core; 7z/rar via optional extras.

See `LIMITATIONS.md` for deliberate tradeoffs (read it before "fixing" surprising
behaviour like tar showing a 0.00 compression ratio).

## Decision tree

| User intent | Call | Returns |
|---|---|---|
| List/summarise without extracting | `zipmonkey.inspect(path)` | `InspectReport` |
| Extract to a directory | `zipmonkey.extract(path, dest)` | `ExtractResult` |
| Extract + auto-clean temp dir | `with zipmonkey.open(path) as a: a.extract()` | `ExtractResult` |
| Get files tagged for dispatch | `zipmonkey.walk_typed(path, dest)` | iter of `TypedFile` |
| Only certain files | `extract(path, dest, include="*.csv")` | `ExtractResult` |
| Drop certain files | `extract(path, dest, exclude="*.log")` | `ExtractResult` |
| Flatten to one folder | `extract(path, dest, flat=True)` | `ExtractResult` |
| Unpack nested archives | `extract(path, dest, recursive=True)` | `ExtractResult` |
| Identify one file's type | `zipmonkey.detect_type(data, filename=name)` | `str` |
| Is a member OS junk? | `zipmonkey.is_os_artifact(name)` | `bool` |

## Failure modes already handled (stop reinventing these)

- `__MACOSX/`, `.DS_Store`, AppleDouble `._*`, `Thumbs.db`, `desktop.ini` stripped by default.
- Path traversal — `..` escapes, NUL/control chars, and symlink-prefix escapes skipped; absolutes re-rooted under `dest`.
- Nested archives (zip-in-zip, tar.gz-in-zip) unpacked recursively; `include`/`exclude`/`flat` apply through recursion, leaf filters still reach inside containers.
- Decompression-bomb caps enforced *while streaming* (no full-member buffering) for ZIP/tar/gzip/bzip2/xz/rar: `max_total_bytes` (50 GiB) + fan-out cap `max_files` (200k) raise `ArchiveLimitError`; `max_depth` (16) records over-deep archives in `skipped_nested` instead of raising. Optional 7z materialises members in memory (declared-size preflight guards the cap) — use a small `max_total_bytes` for untrusted large 7z.
- Format detected by magic bytes, not extension; corrupt streams raise `UnsupportedArchiveError`, never a raw `EOFError`/`zlib.error`.
- Flatten basename collisions renamed `name (1).ext`, never overwritten — checked against files already on disk too.
- Tar symlinks/hardlinks/devices skipped (recorded in `skipped_links`), not materialised.
- Temp extraction dirs auto-removed on context-manager exit (or via `__del__` backstop).
- Compressed (gzip/bzip2/xz) single files exposed as a one-member archive, sized by streaming.

## Worked examples

```python
import zipmonkey

# Inspect: summary + per-member detail, no extraction.
rep = zipmonkey.inspect("bundle.zip")
rep.format            # "zip"  (or "tar.gz", "7z", ...)
rep.file_count        # 3
rep.total_size        # 2048  (uncompressed bytes)
rep.artifact_count    # 1
rep.entries[0].name           # "data.csv"
rep.entries[0].detected_type  # "csv"
rep.entries[0].is_artifact    # False
rep.entries[0].compression_ratio  # 0.42  (compressed/uncompressed; 1.0 if size 0)

# Extract with auto-cleanup of the temp dir.
with zipmonkey.open("bundle.zip") as arc:
    res = arc.extract()                 # dest=None -> temp dir, removed on exit
    res.count                           # 3
    res.dest                            # PosixPath('/tmp/zipmonkey_ab12cd')
    res.skipped_artifacts               # ['__MACOSX/._data.csv', '.DS_Store']

# Selective + recursive to a real directory you own.
res = zipmonkey.extract("bundle.zip", "out", include="*.csv", recursive=True)
res.extracted          # [PosixPath('out/sub/a.csv'), ...]
res.nested_extracted   # [PosixPath('out/nested/inner.zip')]  (kept on disk)

# Dispatch by type (yields LEAF files only; nested containers are not yielded).
for tf in zipmonkey.walk_typed("bundle.zip", "out"):
    tf.detected_type   # "csv"
    tf.category        # "tabular"  -> hand to dsvmonkey
    # categories: "tabular", "pdf", "excel", "archive", "other"

# ExtractResult skip buckets (each a list, all empty when nothing skipped):
#   skipped_artifacts  skipped_filtered  skipped_unsafe
#   skipped_collisions skipped_existing  skipped_links  skipped_nested
```

Downstream dispatch mapping (`TypedFile.category`):
`tabular` -> dsvmonkey · `pdf` -> pdfmonkey · `excel` -> xldetect/xlfilldown.

## Don't

- Don't `open()`/`zipfile` the file first to peek — `inspect()` reads it once.
- Don't pass `clean_artifacts=False` unless you specifically want the `__MACOSX` junk.
- Don't pass an extension-less name to `detect_type` for csv/xlsx — it needs the extension (see LIMITATIONS).
- Don't expect tar `compression_ratio` to be meaningful — tar has no per-member compressed size (LIMITATIONS).
- Don't delete nested archives expecting recursion to have removed them — it keeps sources; remove via `result.nested_extracted`.
- Don't call `extract(path)` (no dest) outside a `with` block expecting cleanup — only the context manager removes temp dirs.

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `UnsupportedArchiveError` | Not a recognised/corrupt format, or 7z/rar extra missing | `pip install zipmonkey[sevenzip]` / `[rar]` |
| `ArchiveLimitError: ... bytes` | Decompression-bomb cap hit | Raise `max_total_bytes` (or set 0) if trusted |
| `ArchiveLimitError: ... file count` | Fan-out (too many files) cap hit | Raise `max_files` (or set 0) if trusted |
| Archive in `skipped_nested` | Nested deeper than `max_depth` (16) | Raise `max_depth`; not an error |
| Member in `skipped_unsafe` | `..`/NUL/symlink-escape path | Expected; the file was a security risk |
| Member in `skipped_links` | tar symlink/device member | Expected; links are not materialised |
| Member in `skipped_existing` | target exists and `overwrite=False` | Expected; remove the target or allow overwrite |
| csv reported as `"text"` | No `.csv` extension supplied | Pass `filename=` to `detect_type` |
| tar `ratio` is `0.00` | tar has no per-member compressed size | Expected (LIMITATIONS) |
| Empty `extracted`, all in `skipped_filtered` | `include` glob matched nothing | Check pattern (matched against path *and* basename, case-insensitive) |

## Testing code that uses this library

- Round-trip: build an archive with stdlib `zipfile`/`tarfile`, extract with zipmonkey, assert file set/bytes match.
- Cross-API: `inspect().file_count` should equal `len(extract().extracted)` when no filters/artifacts apply.
- Golden-file: pin `cli.main(["tree", archive])` stdout (mask volatile temp paths).
