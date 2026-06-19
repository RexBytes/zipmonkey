"""File-type detection from magic bytes, with an extension fallback.

This module exists because archive members frequently have wrong or missing
extensions, so callers need to know *what a file actually is* before
dispatching it to the right processor. Detection is content-first (magic
bytes) and falls back to the filename only when the bytes are ambiguous
(plain text has no magic number).
"""

from __future__ import annotations

import codecs

# (offset, signature, type-label). Order matters only for human readability;
# the matcher checks every entry and the most specific wins by construction
# because signatures do not overlap at the same offset.
_SIGNATURES: tuple[tuple[int, bytes, str], ...] = (
    (0, b"PK\x03\x04", "zip"),
    (0, b"PK\x05\x06", "zip"),  # empty zip
    (0, b"PK\x07\x08", "zip"),  # spanned zip
    (0, b"\x1f\x8b", "gzip"),
    (0, b"BZh", "bzip2"),
    (0, b"\xfd7zXZ\x00", "xz"),
    (0, b"7z\xbc\xaf\x27\x1c", "7z"),
    (0, b"Rar!\x1a\x07\x00", "rar"),
    (0, b"Rar!\x1a\x07\x01\x00", "rar"),
    (0, b"%PDF-", "pdf"),
    (0, b"\x89PNG\r\n\x1a\n", "png"),
    (0, b"\xff\xd8\xff", "jpeg"),
    (0, b"GIF87a", "gif"),
    (0, b"GIF89a", "gif"),
    (0, b"SQLite format 3\x00", "sqlite"),
    (0, b"%!PS", "postscript"),
    (0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "ole"),  # legacy Office (xls/doc/ppt)
    (257, b"ustar", "tar"),  # POSIX/GNU tar magic lives at offset 257
)

# Extension -> type when magic is absent (textual / format-by-convention).
_EXT_TYPES: dict[str, str] = {
    ".csv": "csv",
    ".tsv": "tsv",
    ".psv": "psv",
    ".txt": "text",
    ".json": "json",
    ".xml": "xml",
    ".html": "html",
    ".htm": "html",
    ".md": "text",
    ".log": "text",
    ".yaml": "yaml",
    ".yml": "yaml",
}

# Extension overrides for zip-based containers, which share a single magic
# number with the generic zip container. These resolve to their own type labels
# so recursive extraction treats them as leaf files rather than unpacking them
# as raw zips.
_ZIP_OFFICE: dict[str, str] = {
    ".xlsx": "xlsx",
    ".xlsm": "xlsm",
    ".docx": "docx",
    ".pptx": "pptx",
    ".jar": "jar",
    ".war": "war",
    ".ear": "ear",
    ".apk": "apk",
}
_OLE_OFFICE: dict[str, str] = {
    ".xls": "xls",
    ".doc": "doc",
    ".ppt": "ppt",
}

_CATEGORY: dict[str, str] = {
    "csv": "tabular",
    "tsv": "tabular",
    "psv": "tabular",
    "pdf": "pdf",
    "xlsx": "excel",
    "xlsm": "excel",
    "xls": "excel",
    "zip": "archive",
    "tar": "archive",
    "gzip": "archive",
    "bzip2": "archive",
    "xz": "archive",
    "7z": "archive",
    "rar": "archive",
}

_ARCHIVE_TYPES = frozenset(
    {"zip", "tar", "gzip", "bzip2", "xz", "7z", "rar"}
)

# Filename extensions that indicate an unpackable archive. Used to detect nested
# archives WITHOUT reading content, for backends whose peek would materialise
# the whole member (7z). Office/Java zip containers (.xlsx/.jar) are absent on
# purpose, so they stay leaves.
_ARCHIVE_EXTS = (
    ".zip",
    ".tar",
    ".tgz",
    ".tbz2",
    ".tbz",
    ".txz",
    ".gz",
    ".bz2",
    ".bz",
    ".xz",
    ".lzma",
    ".7z",
    ".rar",
)


def looks_like_archive(filename: str) -> bool:
    """Return True if ``filename`` has an unpackable-archive extension.

    A content-free heuristic for nested-archive detection on backends that
    cannot peek cheaply. ``.xlsx``/``.docx``/``.jar`` etc. are deliberately
    excluded so document/Java zip containers are treated as leaves.
    """
    # rstrip(".") to match _extension's trailing-dot handling.
    base = filename.replace("\\", "/").rsplit("/", 1)[-1].rstrip(".").lower()
    return base.endswith(_ARCHIVE_EXTS)


def _extension(filename: str | None) -> str:
    """Return the lowercased final extension of ``filename`` (incl. dot).

    Trailing dots are stripped first, so ``"report.xlsx."`` yields ``".xlsx"``
    rather than ``"."``.
    """
    if not filename:
        return ""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].rstrip(".")
    dot = name.rfind(".")
    if dot <= 0:  # no dot, or dotfile like ".bashrc"
        return ""
    return name[dot:].lower()


def _looks_textual(data: bytes) -> bool:
    """Heuristic: True if ``data`` looks like text rather than binary.

    A NUL byte in the sample is treated as a hard binary signal (so UTF-16,
    which is full of NULs, reads as non-textual). Otherwise the sample must
    decode as UTF-8 — using an *incremental* decoder so that a multi-byte
    character split across the end of a fixed-size sample window does not cause
    a false "binary" verdict. Genuinely invalid bytes still fail. Empty input
    is not textual (there is nothing to classify).
    """
    if not data:
        return False
    if b"\x00" in data:
        return False
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        # final=False tolerates a trailing partial multi-byte sequence.
        decoder.decode(data, False)
    except UnicodeDecodeError:
        return False
    return True


def detect_type(data: bytes, *, filename: str | None = None) -> str:
    """Detect a file's type from its leading bytes, falling back to extension.

    Detection order:

    1. **Magic bytes.** If ``data`` matches a known signature, that type is
       returned. Zip and OLE containers are refined by extension when the
       filename names an Office document (``.xlsx`` -> ``"xlsx"``,
       ``.xls`` -> ``"xls"``, etc.), since those formats share one magic
       number with their generic container.
    2. **Extension.** When no signature matches, a known textual/by-convention
       extension wins (``.csv`` -> ``"csv"``, ``.json`` -> ``"json"``).
    3. **Textual heuristic.** Content with no NUL bytes that decodes as UTF-8
       is reported as ``"text"``.
    4. **Fallback.** Everything else is ``"unknown"``.

    Compound formats are reported at the magic level: a ``.tar.gz`` payload is
    detected as ``"gzip"`` (the outer container), not ``"tar"`` — distinguishing
    them requires opening the stream, which is the job of the archive layer.

    Args:
        data: The file's leading bytes. 262 bytes is enough to match every
            signature (the tar magic ends at offset 262); more is harmless and
            improves the textual heuristic. May be empty, in which case
            detection relies entirely on ``filename``.
        filename: Optional name used to refine container types and to classify
            extension-only formats. Path separators are tolerated.

    Returns:
        A short lowercase type label. Archive labels are a subset reported by
        :func:`is_archive_type`.
    """
    ext = _extension(filename)

    for offset, sig, label in _SIGNATURES:
        if len(data) >= offset + len(sig) and data[offset : offset + len(sig)] == sig:
            if label == "zip" and ext in _ZIP_OFFICE:
                return _ZIP_OFFICE[ext]
            if label == "ole":
                return _OLE_OFFICE.get(ext, "ole")
            return label

    if ext in _ZIP_OFFICE:
        # Truncated/empty Office file but a clear extension: trust the name.
        return _ZIP_OFFICE[ext]
    if ext in _EXT_TYPES:
        return _EXT_TYPES[ext]

    if _looks_textual(data):
        return "text"

    return "unknown"


def is_archive_type(type_label: str) -> bool:
    """Return True if ``type_label`` names a container we can unpack.

    The set is ``{zip, tar, gzip, bzip2, xz, 7z, rar}``. Note ``gzip``,
    ``bzip2``, and ``xz`` may wrap either a tar stream or a single file; the
    archive layer resolves which at open time.
    """
    return type_label in _ARCHIVE_TYPES


def category_for(type_label: str) -> str:
    """Map a detected type to a coarse dispatch bucket.

    Returns one of ``"tabular"``, ``"pdf"``, ``"excel"``, ``"archive"``, or
    ``"other"``. The buckets correspond to downstream ecosystem packages
    (tabular -> dsvmonkey, pdf -> pdfmonkey, excel -> xldetect/xlfilldown).
    """
    return _CATEGORY.get(type_label, "other")
