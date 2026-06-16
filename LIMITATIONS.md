# LIMITATIONS

Deliberate design decisions that produce behaviour a reviewer might mistake for
a defect. Each entry is a decision we would make again, not an accident we
haven't fixed. Grouped by *why* it isn't being changed.

**Maintenance rule:** when a limitation is fixed, delete its entry — do not
leave "fixed in vX" breadcrumbs. Git history carries the past; this file
describes only the current library.

---

## Fundamental ambiguity (no correct answer without content understanding)

### CSV/TSV detection is extension-based
- **Concern:** `detect_type(b"a,b\n1,2\n")` returns `"text"`, not `"csv"`, unless a `.csv` filename is supplied.
- **Decision:** Tabular types are decided by extension; magic-byte detection only ever yields `"text"` for delimited content.
- **Rationale:** A comma-separated file is byte-structurally indistinguishable from prose containing commas. Guessing "csv" from content would mislabel ordinary text and corrupt downstream dispatch. We require the extension as positive evidence.
- **Escape hatch:** Pass the real name: `detect_type(data, filename="x.csv")`, or rename the file before walking.

### Office formats (xlsx/docx/pptx) need their extension
- **Concern:** A `.xlsx` with a stripped extension is reported as `"zip"`, not `"xlsx"`.
- **Decision:** Zip-based Office types are refined from the filename; bare magic yields `"zip"`.
- **Rationale:** OOXML files *are* ZIP containers — identical magic number. Distinguishing them requires opening the zip and reading `[Content_Types].xml`, doubling I/O for every member during inspection. The extension is the cheap, reliable signal.
- **Escape hatch:** Keep the extension, or open the inner zip yourself and inspect `[Content_Types].xml`.

### Compound archives report the outer layer
- **Concern:** `detect_type` on a `.tar.gz` payload returns `"gzip"`, not `"tar.gz"`.
- **Decision:** `detect_type` works at the magic-byte layer and reports the outermost container only.
- **Rationale:** Determining that a gzip stream wraps a tar (versus a single file) requires decompressing and parsing the stream — that is the archive layer's job, not a byte-sniff. `Archive.format` (which *does* open the stream) reports `"tar.gz"` correctly.
- **Escape hatch:** Use `zipmonkey.open(path).format` for the resolved compound format.

---

## Cost-of-fix exceeds value

### Tar members have no per-member compressed size
- **Concern:** For tar/tar.gz archives, every `ArchiveEntry.compressed_size` is `0`, so `InspectReport.compression_ratio` reads `0.00`.
- **Decision:** Report `0` compressed size for tar members and let the ratio reflect it.
- **Rationale:** Tar compresses the *whole stream*, not individual members, so there is no per-member compressed size to report — the information does not exist in the format. Synthesising a plausible-looking number (e.g. prorating the stream size) would invent data the format never recorded. `ArchiveEntry.size` (uncompressed) is always accurate.
- **Escape hatch:** For an overall figure, compare `os.path.getsize(archive)` against `InspectReport.total_size` yourself.

### Single-file gzip/bzip2/xz is decompressed into memory to size it
- **Concern:** Inspecting a lone `data.csv.gz` reads and decompresses the entire file to report its uncompressed size.
- **Decision:** Materialise the decompressed bytes once (cached) for single-file streams.
- **Rationale:** The gzip/bzip2/xz formats do not store the uncompressed size in a trailer we can trust portably (gzip's ISIZE is mod-2^32 and absent for the other two). Streaming-count would avoid the memory cost but complicate every read path for a format that, by definition, holds one file. For genuinely huge single-file streams, treat the member as a stream yourself.
- **Escape hatch:** Decompress with stdlib `gzip`/`bz2`/`lzma` directly when the payload exceeds available memory.

### ZIP AES encryption is unsupported
- **Concern:** A password-protected ZIP using WinZip AES fails to read even with the correct password.
- **Decision:** Support only what stdlib `zipfile` supports (legacy ZipCrypto).
- **Rationale:** `zipfile` cannot decrypt AES; adding it means a hard dependency on `pyzipper`. That contradicts the minimal-dependency philosophy for a feature most archives don't use. 7z and rar passwords work through their optional backends.
- **Escape hatch:** `pip install pyzipper` and open the archive with it directly.

---

## Behaviour is the contract (changing the default would break callers)

### Absolute member paths are re-rooted, not rejected
- **Concern:** A member named `/etc/passwd` is extracted to `<dest>/etc/passwd` rather than skipped as unsafe.
- **Decision:** Strip leading slashes/drive specifiers and re-root under `dest`; only `..` escapes are skipped (`skipped_unsafe`).
- **Rationale:** Re-rooting absolute paths is the long-standing safe behaviour of `zipfile.extractall` and `tar --extract`; callers expect the files to appear under `dest`. Skipping them instead would silently drop legitimate files from archives created on systems that stored absolute paths. The dangerous case — escaping `dest` via `..` — *is* blocked.
- **Escape hatch:** Inspect `ExtractResult` and re-validate paths, or pre-filter `namelist()` before extracting.

### Recursive extraction keeps the nested archive on disk
- **Concern:** After `recursive=True`, the inner `nested/inner.zip` still exists alongside its unpacked `inner.zip_extracted/` directory.
- **Decision:** Never delete source archives during recursion.
- **Rationale:** Deleting inputs is destructive and irreversible; a caller who wanted the nested archive (e.g. to re-process or checksum it) would lose it with no recovery. The `nested_extracted` list tells you exactly which files were unpacked if you want to remove them.
- **Escape hatch:** `for p in result.nested_extracted: p.unlink()` after extraction.

### File/directory name clashes are skipped, not renamed
- **Concern:** An archive containing both `foo` (a file) and `foo/bar` extracts only one of them; the loser lands in `ExtractResult.skipped_collisions`.
- **Decision:** Write whichever member comes first; skip the one whose path is blocked by the other and record it.
- **Rationale:** A name used as both a file and a directory cannot coexist on a normal filesystem — there is no target path for both. Renaming one would invent a path the archive never specified and break callers that look files up by their archived name. Raising would abort an otherwise-fine extraction over a rare malformed input. Skipping is recoverable and visible.
- **Escape hatch:** Inspect `result.skipped_collisions` and re-extract the clashing member to a different `dest`, or read it directly with `Archive.read(name)`.

### Duplicate flat-mode basenames are renamed, not overwritten
- **Concern:** Three members all named `data.csv` produce `data.csv`, `data (1).csv`, `data (2).csv` rather than one file.
- **Decision:** Suffix `" (n)"` before the extension on collision.
- **Rationale:** Flattening intentionally discards directory structure, so basename collisions are expected, not exceptional. Overwriting would silently lose data; raising would make `flat=True` unusable on any archive with repeated names. Renaming preserves every file with a predictable scheme.
- **Escape hatch:** Use `flat=False` (the default) to preserve the directory tree and avoid collisions entirely.
