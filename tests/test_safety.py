"""Contract tests for path-traversal and limit guards."""

from __future__ import annotations

from pathlib import Path

import pytest

from zipmonkey.safety import (
    ArchiveLimitError,
    check_depth,
    check_total_bytes,
    safe_target,
)


def test_safe_simple_relative(tmp_path):
    target = safe_target(tmp_path, "sub/file.txt")
    assert target == tmp_path / "sub" / "file.txt"


def test_absolute_path_rerooted(tmp_path):
    # Documented contract: absolutes are stripped and re-rooted, not rejected.
    target = safe_target(tmp_path, "/etc/passwd")
    assert target == tmp_path / "etc" / "passwd"


def test_parent_escape_rejected(tmp_path):
    assert safe_target(tmp_path, "../escape.txt") is None


def test_deep_parent_escape_rejected(tmp_path):
    assert safe_target(tmp_path, "a/../../escape.txt") is None


def test_internal_dotdot_that_stays_inside_is_allowed(tmp_path):
    # a/b/../c normalises to a/c, which is inside dest.
    target = safe_target(tmp_path, "a/b/../c.txt")
    assert target == tmp_path / "a" / "c.txt"


def test_empty_and_dot_rejected(tmp_path):
    assert safe_target(tmp_path, "") is None
    assert safe_target(tmp_path, ".") is None
    assert safe_target(tmp_path, "/") is None


def test_windows_drive_rejected(tmp_path):
    assert safe_target(tmp_path, "C:windows/system32") is None


def test_backslash_traversal_rejected(tmp_path):
    assert safe_target(tmp_path, "..\\..\\escape.txt") is None


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


def test_check_depth_under_limit():
    check_depth(5, 16)


def test_check_depth_at_limit():
    check_depth(16, 16)  # at limit allowed


def test_check_depth_over_limit():
    with pytest.raises(ArchiveLimitError):
        check_depth(17, 16)


def test_check_depth_disabled():
    check_depth(9999, 0)
