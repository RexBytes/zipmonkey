"""zipmonkey: smart archive inspection and extraction.

Public API (import directly from ``zipmonkey``):

* :func:`open` / :class:`Archive` — the primary handle (context manager).
* :func:`inspect` — summarise an archive without extracting.
* :func:`extract` — one-shot extraction with filtering/flattening/recursion.
* :func:`walk_typed` — stream extracted files tagged for dispatch.
* :func:`detect_type` — magic-byte file-type detection.
* :func:`is_os_artifact` — OS-junk member predicate.
* Result types: :class:`ArchiveEntry`, :class:`InspectReport`,
  :class:`ExtractResult`, :class:`TypedFile`.

See ``LIMITATIONS.md`` for deliberate design tradeoffs.
"""

from __future__ import annotations

from .archive import Archive, UnsupportedArchiveError, open
from .artifacts import is_os_artifact
from .detect import category_for, detect_type, is_archive_type
from .extract import extract
from .inspect import inspect
from .models import ArchiveEntry, ExtractResult, InspectReport, TypedFile
from .safety import ArchiveLimitError, UnsafePathError
from .walk import walk_typed

__version__ = "0.1.0"

__all__ = [
    "Archive",
    "open",
    "inspect",
    "extract",
    "walk_typed",
    "detect_type",
    "is_archive_type",
    "category_for",
    "is_os_artifact",
    "ArchiveEntry",
    "InspectReport",
    "ExtractResult",
    "TypedFile",
    "UnsupportedArchiveError",
    "UnsafePathError",
    "ArchiveLimitError",
    "__version__",
]
