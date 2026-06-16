"""Contract tests for path-traversal and limit guards."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from zipmonkey.safety import (
    ArchiveLimitError,
    check_file_count,
    check_total_bytes,
    safe_target,
)


def _real(p) -> Path:
    return Path(os.path.realpath(p))


def test_safe_simple_relative(tmp_path):
    target = safe_target(tmp_path, "sub/file.txt")
    assert target == _real(tmp_path) / "sub" / "file.txt"


def test_absolute_path_rerooted(tmp_path):
    # Documented contract: absolutes are stripped and re-rooted, not rejected.
    target = safe_target(tmp_path, "/etc/passwd")
    assert target == _real(tmp_path) / "etc" / "passwd"
    # And it genuinely lands inside dest, not at the real /etc.
    assert target.is_relative_to(_real(tmp_path))


def test_parent_escape_rejected(tmp_path):
    assert safe_target(tmp_path, "../escape.txt") is None


def test_deep_parent_escape_rejected(tmp_path):
    assert safe_target(tmp_path, "a/../../escape.txt") is None


def test_internal_dotdot_that_stays_inside_is_allowed(tmp_path):
    # a/b/../c normalises to a/c, which is inside dest.
    target = safe_target(tmp_path, "a/b/../c.txt")
    assert target == _real(tmp_path) / "a" / "c.txt"


def test_empty_and_dot_rejected(tmp_path):
    assert safe_target(tmp_path, "") is None
    assert safe_target(tmp_path, ".") is None
    assert safe_target(tmp_path, "/") is None


def test_windows_drive_rejected(tmp_path):
    assert safe_target(tmp_path, "C:windows/system32") is None


def test_backslash_traversal_rejected(tmp_path):
    assert safe_target(tmp_path, "..\\..\\escape.txt") is None


def test_nul_byte_rejected(tmp_path):
    assert safe_target(tmp_path, "a\x00b.txt") is None


def test_control_char_rejected(tmp_path):
    assert safe_target(tmp_path, "a\x01b.txt") is None


def test_symlinked_dest_prefix_escape_rejected(tmp_path):
    # A symlink in the existing prefix that redirects outside dest is rejected.
    outside = tmp_path / "outside"
    outside.mkdir()
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "link").symlink_to(outside, target_is_directory=True)
    # "link/x" resolves (via realpath) to outside/x, which escapes dest.
    assert safe_target(dest, "link/x") is None


def test_check_total_bytes_under_limit():
    check_total_bytes(100, 1000)  # no raise


def test_check_total_bytes_at_limit():
    check_total_bytes(1000, 1000)  # exactly at limit: allowed


def test_check_total_bytes_over_limit():
    with pytest.raises(ArchiveLimitError):
        check_total_bytes(1001, 1000)


def test_check_total_bytes_disabled():
    check_total_bytes(10**12, 0)  # 0 disables
    check_total_bytes(10**12, -1)


def test_check_total_bytes_attaches_partial():
    sentinel = object()
    with pytest.raises(ArchiveLimitError) as exc:
        check_total_bytes(2, 1, partial_result=sentinel)
    assert exc.value.partial_result is sentinel


def test_check_file_count_under_limit():
    check_file_count(5, 10)


def test_check_file_count_at_limit():
    check_file_count(10, 10)


def test_check_file_count_over_limit():
    with pytest.raises(ArchiveLimitError):
        check_file_count(11, 10)


def test_check_file_count_disabled():
    check_file_count(10**9, 0)
