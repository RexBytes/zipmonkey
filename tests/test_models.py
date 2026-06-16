"""Contract tests for dataclass-derived properties (ratios, counts)."""

from __future__ import annotations

from pathlib import Path

from zipmonkey.models import ArchiveEntry, ExtractResult, InspectReport


def test_version_is_resolved_string():
    import zipmonkey

    assert isinstance(zipmonkey.__version__, str)
    assert zipmonkey.__version__  # non-empty; from package metadata when installed


def _entry(name="f", size=100, csize=25, is_dir=False, artifact=False, dtype=None):
    return ArchiveEntry(
        name=name,
        size=size,
        compressed_size=csize,
        is_dir=is_dir,
        is_artifact=artifact,
        detected_type=dtype,
    )


def test_is_special_defaults_false():
    assert _entry().is_special is False


def test_compression_ratio_normal():
    assert _entry(size=100, csize=25).compression_ratio == 0.25


def test_compression_ratio_no_compression():
    assert _entry(size=100, csize=100).compression_ratio == 1.0


def test_compression_ratio_expanded():
    assert _entry(size=10, csize=20).compression_ratio == 2.0


def test_compression_ratio_zero_size_is_one():
    # Boundary: nothing to compress -> 1.0, never a ZeroDivisionError.
    assert _entry(size=0, csize=0).compression_ratio == 1.0


def test_inspect_file_count_excludes_dirs():
    rep = InspectReport(
        path=Path("x.zip"),
        format="zip",
        entries=[_entry("a"), _entry("d/", is_dir=True), _entry("b")],
        total_size=200,
        total_compressed=50,
        artifact_count=0,
    )
    assert rep.file_count == 2


def test_inspect_ratio_zero_total_is_one():
    rep = InspectReport(
        path=Path("x.zip"),
        format="zip",
        entries=[],
        total_size=0,
        total_compressed=0,
        artifact_count=0,
    )
    assert rep.compression_ratio == 1.0


def test_inspect_ratio_normal():
    rep = InspectReport(
        path=Path("x.zip"),
        format="zip",
        entries=[],
        total_size=400,
        total_compressed=100,
        artifact_count=0,
    )
    assert rep.compression_ratio == 0.25


def test_extract_result_count_matches_extracted():
    r = ExtractResult(dest=Path("d"), extracted=[Path("a"), Path("b")])
    assert r.count == 2


def test_extract_result_defaults_are_independent():
    r1 = ExtractResult(dest=Path("d1"))
    r2 = ExtractResult(dest=Path("d2"))
    r1.extracted.append(Path("x"))
    assert r2.extracted == []  # no shared mutable default
