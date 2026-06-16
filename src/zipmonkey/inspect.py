"""Top-level ``inspect`` convenience function.

This module exists so callers can summarise an archive in one call without
managing an :class:`~zipmonkey.archive.Archive` handle themselves.
"""

from __future__ import annotations

from pathlib import Path

from .archive import Archive
from .models import InspectReport


def inspect(
    path: str | Path, *, password: bytes | None = None, detect_types: bool = True
) -> InspectReport:
    """Open ``path`` and return an :class:`InspectReport` without extracting.

    Args:
        path: Archive to inspect.
        password: Optional password (bytes) for encrypted archives.
        detect_types: When True, populate each member's ``detected_type`` by
            reading its leading bytes.

    Returns:
        An :class:`~zipmonkey.models.InspectReport`.
    """
    with Archive(path, password=password) as arc:
        return arc.inspect(detect_types=detect_types)
