"""zipmonkey: smart archive inspection and extraction.

Public API (import directly from ``zipmonkey``):

* :func:`open` / :class:`Archive` — the primary handle (context manager).
* :func:`inspect` — summarise an archive without extracting.
* :func:`extract` — one-shot extraction with filtering/flattening/recursion.
* :func:`walk_typed` — stream extracted files tagged for dispatch.
* :func:`detect_type` / :func:`is_archive_type` / :func:`category_for` —
  magic-byte file-type detection and dispatch bucketing.
* :func:`is_os_artifact` — OS-junk member predicate.
* Result types: :class:`ArchiveEntry`, :class:`InspectReport`,
  :class:`ExtractResult`, :class:`TypedFile`.
* Exceptions: :class:`UnsupportedArchiveError`, :class:`ArchiveReadError`,
  :class:`ArchiveLimitError`.

See ``LIMITATIONS.md`` for deliberate design tradeoffs.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from .archive import Archive, ArchiveReadError, UnsupportedArchiveError, open
from .artifacts import is_os_artifact
from .detect import category_for, detect_type, is_archive_type
from .extract import extract
from .inspect import inspect
from .models import ArchiveEntry, ExtractResult, InspectReport, TypedFile
from .safety import ArchiveLimitError
from .walk import walk_typed

try:
    # Single source of truth is pyproject.toml; read it from installed metadata.
    __version__ = _pkg_version("zipmonkey")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"

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
    "ArchiveReadError",
    "ArchiveLimitError",
    "__version__",
]
