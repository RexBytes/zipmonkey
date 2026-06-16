"""Contract tests for magic-byte detection and category mapping."""

from __future__ import annotations

import pytest

from zipmonkey.detect import (
    category_for,
    detect_type,
    is_archive_type,
)


@pytest.mark.parametrize(
    "data,expected",
    [
        (b"PK\x03\x04rest", "zip"),
        (b"PK\x05\x06", "zip"),
        (b"\x1f\x8b\x08", "gzip"),
        (b"BZh91", "bzip2"),
        (b"\xfd7zXZ\x00", "xz"),
        (b"7z\xbc\xaf\x27\x1c", "7z"),
        (b"Rar!\x1a\x07\x00", "rar"),
        (b"Rar!\x1a\x07\x01\x00", "rar"),
        (b"%PDF-1.7", "pdf"),
        (b"\x89PNG\r\n\x1a\n", "png"),
        (b"\xff\xd8\xff\xe0", "jpeg"),
        (b"GIF89a", "gif"),
        (b"SQLite format 3\x00", "sqlite"),
    ],
)
def test_magic_signatures(data, expected):
    assert detect_type(data) == expected


def test_tar_magic_at_offset_257():
    data = b"\x00" * 257 + b"ustar\x00" + b"\x00" * 100
    assert detect_type(data) == "tar"


def test_tar_magic_not_at_offset_0():
    # "ustar" at offset 0 must NOT be detected as tar (adversarial position).
    assert detect_type(b"ustar at the start") != "tar"


def test_zip_refined_to_xlsx_by_extension():
    assert detect_type(b"PK\x03\x04", filename="book.xlsx") == "xlsx"
    assert detect_type(b"PK\x03\x04", filename="doc.docx") == "docx"


def test_xlsm_is_its_own_type():
    # .xlsm is macro-enabled Excel; it must not collapse to "xlsx".
    assert detect_type(b"PK\x03\x04", filename="macro.xlsm") == "xlsm"
    assert category_for("xlsm") == "excel"


def test_utf8_multibyte_split_at_sample_boundary_is_text():
    # A 3-byte euro sign cut after its first byte must NOT read as binary.
    sample = b"x" * 519 + "€".encode("utf-8")[:1]
    assert detect_type(sample) == "text"


def test_trailing_dot_does_not_defeat_extension():
    assert detect_type(b"PK\x03\x04", filename="book.xlsx.") == "xlsx"
    assert detect_type(b"a,b\n", filename="data.csv.") == "csv"


def test_zip_without_office_ext_stays_zip():
    assert detect_type(b"PK\x03\x04", filename="archive.zip") == "zip"


def test_ole_refined_to_xls():
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    assert detect_type(ole, filename="old.xls") == "xls"
    assert detect_type(ole) == "ole"


def test_csv_requires_extension():
    # Fundamental ambiguity: comma text is not csv without the name.
    assert detect_type(b"a,b\n1,2\n") == "text"
    assert detect_type(b"a,b\n1,2\n", filename="data.csv") == "csv"


def test_textual_heuristic():
    assert detect_type(b"just some plain words") == "text"


def test_nul_byte_is_binary_not_text():
    assert detect_type(b"abc\x00def") == "unknown"


def test_empty_data_unknown_without_name():
    assert detect_type(b"") == "unknown"


def test_empty_data_uses_extension():
    assert detect_type(b"", filename="x.json") == "json"


def test_invalid_utf8_unknown():
    assert detect_type(b"\xff\xfe\xfa\xfb") == "unknown"


def test_extension_office_without_magic():
    # Truncated/empty xlsx but clear extension -> trust the name.
    assert detect_type(b"", filename="report.xlsx") == "xlsx"


@pytest.mark.parametrize(
    "label,expected",
    [
        ("zip", True),
        ("tar", True),
        ("gzip", True),
        ("bzip2", True),
        ("xz", True),
        ("7z", True),
        ("rar", True),
        ("csv", False),
        ("pdf", False),
        ("unknown", False),
        ("", False),
    ],
)
def test_is_archive_type(label, expected):
    assert is_archive_type(label) is expected


@pytest.mark.parametrize(
    "label,bucket",
    [
        ("csv", "tabular"),
        ("tsv", "tabular"),
        ("psv", "tabular"),
        ("pdf", "pdf"),
        ("xlsx", "excel"),
        ("xlsm", "excel"),
        ("xls", "excel"),
        ("zip", "archive"),
        ("tar", "archive"),
        ("gzip", "archive"),
        ("bzip2", "archive"),
        ("xz", "archive"),
        ("7z", "archive"),
        ("rar", "archive"),
        ("png", "other"),
        ("unknown", "other"),
        ("", "other"),
    ],
)
def test_category_for(label, bucket):
    assert category_for(label) == bucket


def test_dotfile_extension_ignored():
    # ".bashrc" has no real extension; textual content -> text.
    assert detect_type(b"export PATH=1", filename=".bashrc") == "text"
