"""Top-level ``walk_typed`` convenience function.

This module exists so callers can stream type-tagged extracted files in one
call. Because extraction happens to a real directory, the destination is owned
by the caller; pass ``dest`` to control where files land.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from .archive import Archive
from .models import TypedFile


def walk_typed(
    path: str | Path,
    dest: str | Path,
    *,
    password: bytes | None = None,
    recursive: bool = True,
    **extract_kwargs: object,
) -> Iterator[TypedFile]:
    """Extract ``path`` into ``dest`` and yield each file tagged by type.

    A thin wrapper over :meth:`zipmonkey.archive.Archive.walk_typed`. ``dest``
    is mandatory and owned by the caller; the generator is fully materialised
    before the archive closes so all yielded paths remain valid.

    Yields:
        One :class:`~zipmonkey.models.TypedFile` per extracted file.

    Raises:
        TypeError: If ``dest`` is ``None`` (auto-cleanup temp-dir mode would
            delete the files before the caller can use them; use the context
            manager form for that). Raised eagerly, before any extraction.
    """
    if dest is None:
        raise TypeError(
            "dest is required for zipmonkey.walk_typed(); for an auto-cleaned "
            "temp dir use 'with zipmonkey.open(path) as arc: arc.walk_typed()'"
        )
    return _walk_typed(path, dest, password, recursive, extract_kwargs)


def _walk_typed(
    path: str | Path,
    dest: str | Path,
    password: bytes | None,
    recursive: bool,
    extract_kwargs: dict[str, object],
) -> Iterator[TypedFile]:
    with Archive(path, password=password) as arc:
        yield from list(
            arc.walk_typed(dest, recursive=recursive, **extract_kwargs)
        )
