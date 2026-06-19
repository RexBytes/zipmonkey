"""Guards against malicious archives (path traversal and runaway expansion).

This module exists because extracting an untrusted archive is a security
operation, not just an I/O convenience: a member named ``../../etc/cron.d/x``
will overwrite files outside the destination unless the target path is
validated, and a small archive can decompress to a huge tree of files or
bytes. Recursion, total-byte, and total-file limits bound the blast radius of
nested archives, decompression bombs, and fan-out (breadth) bombs.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Defaults chosen to be generous for real archives but bounded against abuse.
DEFAULT_MAX_DEPTH = 16
DEFAULT_MAX_TOTAL_BYTES = 50 * 1024 * 1024 * 1024  # 50 GiB written to disk
DEFAULT_MAX_FILES = 200_000  # bounds fan-out (file/inode) bombs
DEFAULT_MAX_MEMBER_BYTES = 0  # per-member uncompressed cap; 0 = disabled


class ArchiveLimitError(RuntimeError):
    """Raised when an extraction exceeds a configured safety limit.

    The partially-completed :class:`~zipmonkey.models.ExtractResult` (recording
    everything written before the limit tripped) is attached as
    ``partial_result`` so a caller can still clean up or report progress.
    """

    def __init__(self, message: str, *, partial_result: Any | None = None) -> None:
        super().__init__(message)
        self.partial_result = partial_result


def safe_target(dest: Path, member_name: str) -> Path | None:
    """Resolve ``member_name`` under ``dest`` or reject it as unsafe.

    The member name is normalised (backslashes to slashes, redundant ``.`` and
    ``..`` segments collapsed) and joined onto ``dest``. Both the destination
    and the candidate are resolved with :func:`os.path.realpath`, so a symlink
    anywhere in the existing prefix that would redirect the write outside
    ``dest`` causes rejection. The result is returned only if it stays strictly
    within ``dest``; otherwise ``None`` is returned so the caller can skip the
    member.

    Absolute member paths (``/etc/passwd``) are **re-rooted** under ``dest``
    (the leading separator is stripped), matching the long-standing behaviour
    of ``zipfile`` and ``tar``. Windows drive specifiers (``C:...``), ``..``
    escapes, control characters (code points below 32 and DEL 127), and members
    resolving to ``dest`` itself are **rejected** (returning ``None``).

    Args:
        dest: The extraction root. Need not exist yet; its existing prefix is
            resolved for symlinks.
        member_name: The archive member path.

    Returns:
        The absolute, symlink-resolved target path, or ``None`` if the member
        is unsafe.
    """
    dest_root = Path(os.path.realpath(dest))

    normalized = member_name.replace("\\", "/").strip("/")
    if not normalized or normalized == ".":
        return None

    # Reject NUL and other control characters (C0 range plus DEL) that break
    # path APIs / smuggle.
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in normalized):
        return None

    # Reject Windows drive specifiers (e.g. "C:foo") that os.path.join honours.
    first = normalized.split("/", 1)[0]
    if len(first) >= 2 and first[1] == ":":
        return None

    candidate = Path(os.path.realpath(os.path.join(dest_root, normalized)))

    try:
        candidate.relative_to(dest_root)
    except ValueError:
        return None

    if candidate == dest_root:
        return None

    return candidate


def check_total_bytes(
    running_total: int, max_total: int, *, partial_result: Any | None = None
) -> None:
    """Raise :class:`ArchiveLimitError` if ``running_total`` exceeds the cap.

    A ``max_total`` of ``0`` or negative disables the check.
    """
    if max_total > 0 and running_total > max_total:
        raise ArchiveLimitError(
            f"uncompressed output exceeded {max_total} bytes "
            f"(reached {running_total}); raise max_total_bytes to allow",
            partial_result=partial_result,
        )


def check_file_count(
    running_count: int, max_files: int, *, partial_result: Any | None = None
) -> None:
    """Raise :class:`ArchiveLimitError` if ``running_count`` exceeds the cap.

    Bounds fan-out bombs where a small archive expands to a huge *number* of
    files (each tiny enough to evade the byte cap). ``0`` or negative disables.
    """
    if max_files > 0 and running_count > max_files:
        raise ArchiveLimitError(
            f"extracted file count exceeded {max_files}; "
            f"raise max_files to allow",
            partial_result=partial_result,
        )


def check_member_bytes(
    size: int,
    max_member: int,
    name: str,
    *,
    partial_result: Any | None = None,
) -> None:
    """Raise :class:`ArchiveLimitError` if one member's declared size is too big.

    Checked against each member's *declared* uncompressed size before it is
    read, so an oversized member is rejected without being materialised. This
    matters most for non-streaming backends (7z), where peak memory is
    proportional to a single member. ``0`` or negative disables the check.
    """
    if max_member > 0 and size > max_member:
        raise ArchiveLimitError(
            f"member {name!r} uncompressed size {size} exceeds "
            f"max_member_bytes {max_member}",
            partial_result=partial_result,
        )
