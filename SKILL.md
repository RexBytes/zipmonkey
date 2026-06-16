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
- Path traversal (`../escape`, `/abs`) — `..` escapes skipped, absolutes re-rooted under `dest`.
- Nested archives (zip-in-zip, tar.gz-in-zip) unpacked recursively with a depth cap.
- Decompression-bomb cap via `max_total_bytes` (default 50 GiB) and `max_depth` (default 16).
- Format detected by magic bytes, not extension — a mislabelled `.zip` that is really tar still opens.
- Flatten basename collisions renamed `name (1).ext`, never overwritten.
- Temp extraction dirs auto-removed on context-manager exit.
- Compressed (gzip/bzip2/xz) single files exposed as a one-member archive.

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

# Dispatch by type.
for tf in zipmonkey.walk_typed("bundle.zip", "out"):
    tf.detected_type   # "csv"
    tf.category        # "tabular"  -> hand to dsvmonkey
    # categories: "tabular", "pdf", "excel", "archive", "other"
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
| `UnsupportedArchiveError` | Not a recognised format, or 7z/rar extra missing | `pip install zipmonkey[sevenzip]` / `[rar]` |
| `ArchiveLimitError: ... bytes` | Decompression-bomb cap hit | Raise `max_total_bytes` if the archive is trusted |
| `ArchiveLimitError: ... depth` | Nested archives deeper than 16 | Raise `max_depth` if trusted |
| Member in `skipped_unsafe` | `..` path-traversal attempt | Expected; the file was a security risk |
| csv reported as `"text"` | No `.csv` extension supplied | Pass `filename=` to `detect_type` |
| tar `ratio` is `0.00` | tar has no per-member compressed size | Expected (LIMITATIONS) |
| Empty `extracted`, all in `skipped_filtered` | `include` glob matched nothing | Check pattern (matched against path *and* basename, case-insensitive) |

## Testing code that uses this library

- Round-trip: build an archive with stdlib `zipfile`/`tarfile`, extract with zipmonkey, assert file set/bytes match.
- Cross-API: `inspect().file_count` should equal `len(extract().extracted)` when no filters/artifacts apply.
- Golden-file: pin `cli.main(["tree", archive])` stdout (mask volatile temp paths).
