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

### Reading a duplicate member name returns one (unspecified) member
- **Concern:** An archive with two members of the same name (e.g. two `f.txt`) returns only one of them from `read("f.txt")`/`open_member("f.txt")` — the first for 7z, the last for ZIP/tar.
- **Decision:** `read`/`open_member` are by-*name* lookups; for a duplicated name they return whichever member that name resolves to in the underlying library (first vs last differs by backend). Full fidelity is the *extraction* path's job.
- **Rationale:** A by-name API cannot return two different members for one name — there is no key to disambiguate them, and which one is "correct" has no content-independent answer. Inventing positional/index access would bloat the API for a rare, malformed input. Extraction *does* preserve every member: the first is written and each later duplicate is recorded in `ExtractResult.skipped_collisions`, so nothing is silently lost.
- **Escape hatch:** Use `extract()` and inspect `skipped_collisions`, or open the archive with the underlying library (`zipfile`/`tarfile`/`py7zr`) and iterate members positionally.

---

## Cost-of-fix exceeds value

### Tar members (and 7z solid-block members) have no per-member compressed size
- **Concern:** For tar/tar.gz archives every `ArchiveEntry.compressed_size` is `0` (so `InspectReport.compression_ratio` reads `0.00`); for 7z archives written as a *solid* block, py7zr attributes the whole block's packed size to the first member and reports `0` for the rest, so those members also read `ratio 0.00`.
- **Decision:** Report whatever per-member compressed size the format/library exposes (`0` for tar members and trailing 7z solid-block members) and let the ratio reflect it.
- **Rationale:** Tar compresses the *whole stream* and 7z compresses a *whole solid block*, not individual members, so there is no per-member compressed size to report — the information does not exist at that granularity. Synthesising a plausible-looking number (e.g. prorating the stream size) would invent data the format never recorded. `ArchiveEntry.size` (uncompressed) is always accurate, and the archive's overall ratio is meaningful.
- **Escape hatch:** For an overall figure, compare `os.path.getsize(archive)` against `InspectReport.total_size` yourself; write 7z non-solid if you need per-member compressed sizes.

### Inspecting a standalone gzip/bzip2/xz streams the whole payload to size it
- **Concern:** `inspect("huge.gz")` reads the entire decompressed stream to report `total_size`, with no inspect-time byte cap — slow for very large single-file streams.
- **Decision:** Stream-count the decompressed bytes (O(1) memory, O(n) time) to populate `ArchiveEntry.size`.
- **Rationale:** gzip stores only a mod-2³² uncompressed size in its trailer (wrong above 4 GiB) and bzip2 stores none, so there is no portable, trustworthy size to read cheaply. A streaming count is memory-safe (it never materialises the payload — that bomb is already closed), and inspection is an opt-in operation a caller chooses to run. The byte cap belongs to `extract`, which is where untrusted bulk extraction happens.
- **Escape hatch:** Skip inspection and stream the member yourself via `Archive.open_member`, or `os.path.getsize` the compressed file if only the on-disk size matters.

### 7z members are materialised in memory, not streamed
- **Concern:** Unlike ZIP/tar/gzip/bzip2/xz (which stream during extraction and enforce `max_total_bytes` chunk-by-chunk), the optional 7z backend reads each member fully into memory before writing, so peak memory is proportional to one member's uncompressed size; `inspect(detect_types=True)` likewise materialises each 7z member to classify it.
- **Decision:** Accept whole-member materialisation for 7z and guard it with a *declared-size preflight* (the member's header size is checked against `max_total_bytes` before any decompression), plus the `_Backend.streaming = False` flag.
- **Rationale:** `py7zr` exposes only whole-member decompression (`read()` returns a `BytesIO`); there is no public chunked/callback API to stream against. The preflight rejects oversized members before they are decompressed, closing the declared-bomb hole; the residual cost is in-memory size for an *honestly-declared* large member, which only affects callers who opt into 7z. Core stdlib formats are unaffected.
- **Escape hatch:** For untrusted/large 7z, set `max_member_bytes` (a per-member cap that rejects an oversized member before it is decompressed) and/or a small `max_total_bytes`, pass `detect_types=False` to `inspect`, or extract members individually with your own preflight via `Archive.entries()` + `Archive.open_member`.

### Recursive 7z detects nested archives by extension, not content
- **Concern:** With `recursive=True`, a nested archive stored *inside* a 7z under a non-archive name (e.g. a real zip stored as `payload.bin`) is treated as a leaf and not unpacked — whereas the ZIP/tar backends would sniff its magic bytes and recurse.
- **Decision:** For the non-streaming 7z backend, decide "is this a nested archive?" from the member's *extension* (`looks_like_archive`), not by peeking its content; streaming backends (ZIP/tar) still sniff by magic.
- **Rationale:** Peeking a 7z member materialises the *whole* member (py7zr has no streaming API). Sniffing every member that way would decompress members that a leaf filter or the `max_member_bytes` cap should have excluded — which previously made an over-cap, would-be-filtered member raise a spurious `ArchiveLimitError`. Extension-based detection never materialises a member just to classify it, so the filter and caps behave uniformly across all backends. The cost is a mis-named nested 7z member going unrecursed; in practice nested archives carry their real extension, and the member is still extracted as a leaf (no data lost) — you can re-run extraction on it.
- **Escape hatch:** Re-run `extract(recursive=True)` on the leaf path, or extract that member and open it directly.

### Extraction is not TOCTOU-race-proof against a hostile concurrent writer
- **Concern:** `safe_target` resolves symlinks in the existing `dest` prefix before writing, but another process mutating the `dest` tree *during* extraction could in principle defeat the check (a time-of-check/time-of-use race).
- **Decision:** Validate lexically + via `realpath` at write time; do not use directory-fd / `O_NOFOLLOW` atomic operations.
- **Rationale:** The threat model is untrusted *archives*, not an untrusted *filesystem being mutated mid-extraction by another local process*. Closing the race fully requires `openat`/`O_NOFOLLOW` plumbing that the stdlib path APIs don't expose portably, for a scenario that does not arise when `dest` is a private directory (the recommended usage). Single-writer extraction into a directory you control is safe.
- **Escape hatch:** Extract into a freshly-created private directory (e.g. `tempfile.mkdtemp`) that no other process can write to, then move results into place.

### ZIP AES encryption is unsupported
- **Concern:** A password-protected ZIP using WinZip AES fails to read even with the correct password.
- **Decision:** Support only what stdlib `zipfile` supports (legacy ZipCrypto).
- **Rationale:** `zipfile` cannot decrypt AES; adding it means a hard dependency on `pyzipper`. That contradicts the minimal-dependency philosophy for a feature most archives don't use. 7z and rar passwords work through their optional backends.
- **Escape hatch:** `pip install pyzipper` and open the archive with it directly.

---

### `max_total_bytes` / `max_files` count nested containers; `count` does not
- **Concern:** The byte and file caps include nested-archive container files, so an `ArchiveLimitError` can fire while `ExtractResult.count` (leaf files) is still below the cap; the running byte total also exceeds the sum of leaf-file sizes.
- **Decision:** Both caps measure everything written under `dest` (containers included); `ExtractResult.written_count` exposes that figure while `count` stays leaf-only.
- **Rationale:** The caps are disk/decompression/fan-out guards; the quantity that bounds damage is "how much did we write to the filesystem," which is exactly what is counted. Counting only leaves would let a deeply nested tree of containers consume disk/inodes while staying under the cap. `count` is leaf-only because that is what callers dispatch on.
- **Escape hatch:** Compare against `result.written_count`; raise `max_total_bytes`/`max_files` (or set `0`); or extract without `recursive`.

## Behaviour is the contract (changing the default would break callers)

### AppleDouble `._` prefix may flag legitimately-named files
- **Concern:** A user file literally named `._notes.txt` (or a dotfile like `.__init__.py`) is treated as an OS artifact and skipped by default.
- **Decision:** Any basename beginning with `._` is an OS artifact.
- **Rationale:** macOS writes a `._<name>` AppleDouble resource fork for *every* file inside a non-HFS archive, so `._`-prefixed names are overwhelmingly junk. Distinguishing a genuine `._foo` from a resource fork requires checking for a sibling `foo`, which is fragile (the sibling may be filtered out) and still ambiguous. We accept the rare false positive.
- **Escape hatch:** Pass `clean_artifacts=False` to keep every member, then filter yourself.

### Tar special members (symlinks/hardlinks/devices) are skipped
- **Concern:** A tar symlink, hardlink, device, or FIFO member is not recreated; it lands in `ExtractResult.skipped_links`.
- **Decision:** Extract only regular files and directories; record special members and skip them.
- **Rationale:** Materialising a symlink from an untrusted archive is a traversal vector (a link to `/etc` followed by a write "through" it), and recreating devices/FIFOs is rarely what a data pipeline wants. Writing the link's *target bytes* as a regular file (tar's `extractfile` behaviour) would silently duplicate/leak data. Skipping is the safe, predictable choice; `is_special` on the inspect report tells you they exist.
- **Note:** ZIP symlinks are detected from the Unix mode in `ZipInfo.external_attr`; a symlink stored without that metadata (some non-Unix tools) is not recognised and extracts as a regular file containing the target path — harmless (no link is created), just not flagged in `skipped_links`.
- **Escape hatch:** Use stdlib `tarfile` with your own `filter=` if you genuinely need links recreated.

### Nested archives beyond `max_depth` are left on disk, not treated as an error
- **Concern:** With `recursive=True` and a chain deeper than `max_depth`, the deepest container is written but not unpacked; it appears in `skipped_nested` rather than raising.
- **Decision:** Stop descending at the limit and record the un-unpacked container; do not abort the whole extraction.
- **Rationale:** Depth is a structural bound, not a security emergency like the byte/file caps — everything up to the limit is legitimately extracted, and aborting would discard that work. The container is still on disk for manual handling. (The byte and file caps *do* raise, because exceeding them means active abuse in progress.)
- **Escape hatch:** Raise `max_depth`, or re-run extraction on each path in `skipped_nested`.

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

### Nested archives reuse the top-level password
- **Concern:** During recursive extraction, a password-protected nested archive is opened with the *same* password given for the outer archive; there is no per-archive password.
- **Decision:** Propagate the top-level password to every nested archive.
- **Rationale:** The overwhelmingly common case is one password protecting a whole bundle, so reuse "just works." Supporting per-nested passwords would require a callback/mapping API that 99% of callers don't need. A nested archive that needs a *different* password surfaces its library's error at read time (e.g. `RuntimeError: Bad password`) rather than guessing.
- **Escape hatch:** Extract non-recursively, then open each archive in `result.nested_extracted` yourself with its own password.

### Duplicate flat-mode basenames are renamed, not overwritten
- **Concern:** Three members all named `data.csv` produce `data.csv`, `data (1).csv`, `data (2).csv` rather than one file.
- **Decision:** Suffix `" (n)"` before the extension on collision.
- **Rationale:** Flattening intentionally discards directory structure, so basename collisions are expected, not exceptional. Overwriting would silently lose data; raising would make `flat=True` unusable on any archive with repeated names. Renaming preserves every file with a predictable scheme.
- **Escape hatch:** Use `flat=False` (the default) to preserve the directory tree and avoid collisions entirely.
