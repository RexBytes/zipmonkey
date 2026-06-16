"""Top-level ``extract`` convenience function.

This module exists for one-shot extraction without managing an
:class:`~zipmonkey.archive.Archive` handle. Because there is no context to
clean up after, a ``dest`` is required (or a persistent temp dir is returned);
for auto-cleanup use ``with zipmonkey.open(...) as arc`` instead.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .archive import Archive
from .models import ExtractResult
from .safety import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_MEMBER_BYTES,
    DEFAULT_MAX_TOTAL_BYTES,
)


def extract(
    path: str | Path,
    dest: str | Path,
    *,
    password: bytes | None = None,
    include: str | Sequence[str] | None = None,
    exclude: str | Sequence[str] | None = None,
    flat: bool = False,
    recursive: bool = False,
    clean_artifacts: bool = True,
    overwrite: bool = True,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    max_files: int = DEFAULT_MAX_FILES,
    max_member_bytes: int = DEFAULT_MAX_MEMBER_BYTES,
) -> ExtractResult:
    """Extract ``path`` into ``dest``.

    A thin wrapper over :meth:`zipmonkey.archive.Archive.extract`; see that
    method for the full meaning of each keyword. Unlike the context-manager
    form, ``dest`` is mandatory here and is never auto-removed — the caller
    owns the output directory.

    Returns:
        An :class:`~zipmonkey.models.ExtractResult`.
    """
    with Archive(path, password=password) as arc:
        return arc.extract(
            dest,
            include=include,
            exclude=exclude,
            flat=flat,
            recursive=recursive,
            clean_artifacts=clean_artifacts,
            overwrite=overwrite,
            max_depth=max_depth,
            max_total_bytes=max_total_bytes,
            max_files=max_files,
            max_member_bytes=max_member_bytes,
        )
