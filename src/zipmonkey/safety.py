"""Guards against malicious archives (path traversal and runaway recursion).

This module exists because extracting an untrusted archive is a security
operation, not just an I/O convenience: a member named ``../../etc/cron.d/x``
or ``/etc/passwd`` will overwrite files outside the destination unless the
target path is validated. Recursion and total-size limits bound the blast
radius of nested archives and decompression bombs.
"""

from __future__ import annotations

import os
from pathlib import Path

# Defaults chosen to be generous for real archives but bounded against abuse.
DEFAULT_MAX_DEPTH = 16
DEFAULT_MAX_TOTAL_BYTES = 50 * 1024 * 1024 * 1024  # 50 GiB uncompressed


class UnsafePathError(ValueError):
    """Raised when an archive member resolves outside the destination."""


class ArchiveLimitError(RuntimeError):
    """Raised when an extraction exceeds a configured safety limit."""


def safe_target(dest: Path, member_name: str) -> Path | None:
    """Resolve ``member_name`` under ``dest`` or reject it as unsafe.

    The member name is normalised (backslashes to slashes, redundant ``.``
    and ``..`` segments collapsed) and joined onto ``dest``. The result is
    returned only if it stays strictly within ``dest``; otherwise ``None`` is
    returned so the caller can skip the member.

    This rejects absolute paths (``/etc/passwd``), parent-directory escapes
    (``../../x``), and Windows drive/UNC-style absolutes. A member that
    normalises to ``dest`` itself (empty or ``"."``) is rejected too — there is
    no file to write there.

    Args:
        dest: The extraction root. Need not exist yet; it is resolved
            lexically against the current directory.
        member_name: The archive member path.

    Returns:
        The absolute target path, or ``None`` if the member is unsafe.
    """
    dest_root = Path(os.path.abspath(dest))

    normalized = member_name.replace("\\", "/").strip("/")
    if not normalized or normalized == ".":
        return None

    # Reject Windows drive specifiers (e.g. "C:foo") that os.path.join honours.
    first = normalized.split("/", 1)[0]
    if len(first) >= 2 and first[1] == ":":
        return None

    candidate = Path(os.path.normpath(os.path.join(dest_root, normalized)))

    try:
        candidate.relative_to(dest_root)
    except ValueError:
        return None

    if candidate == dest_root:
        return None

    return candidate


def check_total_bytes(running_total: int, max_total: int) -> None:
    """Raise :class:`ArchiveLimitError` if ``running_total`` exceeds the cap.

    A ``max_total`` of ``0`` or negative disables the check.
    """
    if max_total > 0 and running_total > max_total:
        raise ArchiveLimitError(
            f"uncompressed output exceeded {max_total} bytes "
            f"(reached {running_total}); raise max_total_bytes to allow"
        )


def check_depth(depth: int, max_depth: int) -> None:
    """Raise :class:`ArchiveLimitError` if recursion ``depth`` exceeds the cap.

    A ``max_depth`` of ``0`` or negative disables the check.
    """
    if max_depth > 0 and depth > max_depth:
        raise ArchiveLimitError(
            f"nested-archive recursion exceeded depth {max_depth}; "
            f"raise max_depth to allow"
        )
