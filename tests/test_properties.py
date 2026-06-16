"""Property-based round-trip tests: build -> extract -> compare.

The highest-leverage check here is that an archive built from an arbitrary set
of (name, bytes) members extracts back to exactly those bytes, with directory
structure preserved and no member lost or corrupted.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

import zipmonkey
from zipmonkey.detect import category_for, detect_type


def _has_prefix_collision(names) -> bool:
    """True if any name is also a directory prefix of another (foo + foo/bar)."""
    dirs: set[str] = set()
    for n in names:
        parts = n.split("/")
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))
    return any(n in dirs for n in names)

# Member names: safe relative POSIX paths (no traversal, no junk, no control).
_segment = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),
        whitelist_characters="_-",
    ),
    min_size=1,
    max_size=8,
)
_relpath = st.lists(_segment, min_size=1, max_size=3).map("/".join)
_payload = st.binary(min_size=0, max_size=64)


@settings(max_examples=120, suppress_health_check=[HealthCheck.too_slow])
@given(
    members=st.dictionaries(_relpath, _payload, min_size=1, max_size=8),
)
def test_zip_roundtrip(tmp_path_factory, members):
    assume(not _has_prefix_collision(members))
    tmp = tmp_path_factory.mktemp("rt")
    archive = tmp / "a.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)

    dest = tmp / "out"
    # clean_artifacts off so the property isolates round-trip from detection.
    result = zipmonkey.extract(archive, dest, clean_artifacts=False)

    assert result.count == len(members)
    for name, data in members.items():
        path = dest / name
        assert path.read_bytes() == data


@settings(max_examples=120, suppress_health_check=[HealthCheck.too_slow])
@given(members=st.dictionaries(_relpath, _payload, min_size=1, max_size=8))
def test_inspect_total_size_matches_payloads(tmp_path_factory, members):
    assume(not _has_prefix_collision(members))
    tmp = tmp_path_factory.mktemp("rt")
    archive = tmp / "a.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)

    rep = zipmonkey.inspect(archive)
    assert rep.total_size == sum(len(d) for d in members.values())
    assert rep.file_count == len(members)


@settings(max_examples=80, suppress_health_check=[HealthCheck.too_slow])
@given(members=st.dictionaries(_relpath, _payload, min_size=1, max_size=6))
def test_flat_extraction_loses_no_files(tmp_path_factory, members):
    assume(not _has_prefix_collision(members))
    tmp = tmp_path_factory.mktemp("rt")
    archive = tmp / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)

    result = zipmonkey.extract(archive, tmp / "flat", flat=True, clean_artifacts=False)
    # Flatten may rename, but never drops a file.
    assert result.count == len(members)


@settings(max_examples=80, suppress_health_check=[HealthCheck.too_slow])
@given(
    members=st.dictionaries(_relpath, _payload, min_size=1, max_size=6),
    mode=st.sampled_from(["w", "w:gz", "w:bz2", "w:xz"]),
)
def test_tar_roundtrip(tmp_path_factory, members, mode):
    assume(not _has_prefix_collision(members))
    tmp = tmp_path_factory.mktemp("rt")
    archive = tmp / "a.tar"
    with tarfile.open(archive, mode) as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

    dest = tmp / "out"
    result = zipmonkey.extract(archive, dest, clean_artifacts=False)
    assert result.count == len(members)
    for name, data in members.items():
        assert (dest / name).read_bytes() == data


@settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow])
@given(members=st.dictionaries(_relpath, _payload, min_size=1, max_size=5))
def test_recursive_roundtrip_reaches_nested_payloads(tmp_path_factory, members):
    assume(not _has_prefix_collision(members))
    tmp = tmp_path_factory.mktemp("rt")
    inner = tmp / "inner.zip"
    with zipfile.ZipFile(inner, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    outer = tmp / "outer.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.write(inner, "nested/inner.zip")

    dest = tmp / "out"
    zipmonkey.extract(outer, dest, recursive=True, clean_artifacts=False)
    # Each nested member is written under inner.zip_extracted/<name> with its
    # exact bytes (true even if a payload happens to look like an archive: it
    # is written to disk before any recursion decision).
    sub = dest / "nested" / "inner.zip_extracted"
    for name, data in members.items():
        assert (sub / name).read_bytes() == data


@settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow])
@given(members=st.dictionaries(_relpath, _payload, min_size=1, max_size=5))
def test_walk_typed_category_matches_detect(tmp_path_factory, members):
    assume(not _has_prefix_collision(members))
    tmp = tmp_path_factory.mktemp("rt")
    archive = tmp / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)

    for tf in zipmonkey.walk_typed(archive, tmp / "out", clean_artifacts=False):
        # The dispatch invariant: category is always category_for(detected_type).
        assert tf.category == category_for(tf.detected_type)
        assert tf.category == category_for(detect_type(tf.path.read_bytes(), filename=tf.path.name))
