"""Integration contract tests for Archive: open/inspect/extract/walk."""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

import pytest

import zipmonkey
from zipmonkey.archive import Archive, UnsupportedArchiveError


# -- open / format detection ------------------------------------------------ #


def test_open_detects_zip(zip_factory):
    z = zip_factory("a.zip", {"f.txt": b"hi"})
    with zipmonkey.open(z) as arc:
        assert arc.format == "zip"


def test_open_by_magic_not_extension(zip_factory, tmp_path):
    z = zip_factory("real.zip", {"f.txt": b"hi"})
    misnamed = tmp_path / "looks_like.tar"
    misnamed.write_bytes(z.read_bytes())
    with zipmonkey.open(misnamed) as arc:
        assert arc.format == "zip"  # opened by content, not name


def test_open_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        zipmonkey.open(tmp_path / "nope.zip")


def test_open_directory_raises(tmp_path):
    with pytest.raises(UnsupportedArchiveError):
        zipmonkey.open(tmp_path)


def test_open_non_archive_raises(tmp_path):
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"not an archive at all\x00\x01")
    with pytest.raises(UnsupportedArchiveError):
        zipmonkey.open(junk)


@pytest.mark.parametrize(
    "mode,fmt",
    [("w:gz", "tar.gz"), ("w:bz2", "tar.bz2"), ("w:xz", "tar.xz"), ("w", "tar")],
)
def test_tar_formats(tar_factory, mode, fmt):
    t = tar_factory(f"a_{fmt}.tar", {"f.txt": b"hello"}, mode=mode)
    assert zipmonkey.inspect(t).format == fmt


def test_single_gzip_file(tmp_path):
    import gzip

    p = tmp_path / "lone.csv.gz"
    with gzip.open(p, "wb") as f:
        f.write(b"a,b\n1,2\n")
    rep = zipmonkey.inspect(p)
    assert rep.format == "gzip"
    assert rep.file_count == 1
    assert rep.entries[0].name == "lone.csv"
    assert rep.entries[0].detected_type == "csv"


# -- inspect ---------------------------------------------------------------- #


def test_inspect_counts_and_types(messy_zip):
    rep = zipmonkey.inspect(messy_zip)
    assert rep.format == "zip"
    names = {e.name for e in rep.entries}
    assert "report.pdf" in names
    pdf = next(e for e in rep.entries if e.name == "report.pdf")
    assert pdf.detected_type == "pdf"
    assert rep.artifact_count == 2  # __MACOSX/._data.csv and .DS_Store


def test_inspect_total_size_excludes_dirs(zip_factory):
    z = zip_factory("s.zip", {"a.txt": b"x" * 10, "b.txt": b"y" * 20})
    rep = zipmonkey.inspect(z)
    assert rep.total_size == 30


def test_inspect_no_type_detection(zip_factory):
    z = zip_factory("s.zip", {"a.csv": b"a,b\n"})
    with zipmonkey.open(z) as arc:
        rep = arc.inspect(detect_types=False)
    assert rep.entries[0].detected_type is None


# -- extract: basics, filtering, artifacts ---------------------------------- #


def test_extract_basic(zip_factory, tmp_path):
    z = zip_factory("a.zip", {"x.txt": b"hello", "sub/y.txt": b"world"})
    dest = tmp_path / "out"
    res = zipmonkey.extract(z, dest)
    assert res.count == 2
    assert (dest / "x.txt").read_bytes() == b"hello"
    assert (dest / "sub" / "y.txt").read_bytes() == b"world"


def test_extract_strips_artifacts_by_default(messy_zip, tmp_path):
    res = zipmonkey.extract(messy_zip, tmp_path / "out")
    assert "__MACOSX/._data.csv" in res.skipped_artifacts
    assert ".DS_Store" in res.skipped_artifacts
    assert not (tmp_path / "out" / ".DS_Store").exists()


def test_extract_keep_artifacts(messy_zip, tmp_path):
    res = zipmonkey.extract(messy_zip, tmp_path / "out", clean_artifacts=False)
    assert res.skipped_artifacts == []
    assert (tmp_path / "out" / ".DS_Store").exists()


def test_extract_include_glob(messy_zip, tmp_path):
    res = zipmonkey.extract(messy_zip, tmp_path / "out", include="*.csv")
    extracted_names = {p.name for p in res.extracted}
    assert extracted_names == {"a.csv"}
    assert "report.pdf" in res.skipped_filtered


def test_extract_exclude_overrides_include(zip_factory, tmp_path):
    z = zip_factory(
        "a.zip", {"keep.csv": b"1", "drop.csv": b"2", "other.txt": b"3"}
    )
    res = zipmonkey.extract(
        z, tmp_path / "out", include="*.csv", exclude="drop.*"
    )
    assert {p.name for p in res.extracted} == {"keep.csv"}


def test_extract_include_list(zip_factory, tmp_path):
    z = zip_factory("a.zip", {"a.csv": b"1", "b.pdf": b"2", "c.log": b"3"})
    res = zipmonkey.extract(z, tmp_path / "out", include=["*.csv", "*.pdf"])
    assert {p.name for p in res.extracted} == {"a.csv", "b.pdf"}


def test_extract_include_case_insensitive(zip_factory, tmp_path):
    z = zip_factory("a.zip", {"DATA.CSV": b"1"})
    res = zipmonkey.extract(z, tmp_path / "out", include="*.csv")
    assert res.count == 1


def test_extract_overwrite_false_skips_existing(zip_factory, tmp_path):
    z = zip_factory("a.zip", {"x.txt": b"new"})
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "x.txt").write_bytes(b"old")
    res = zipmonkey.extract(z, dest, overwrite=False)
    assert (dest / "x.txt").read_bytes() == b"old"
    assert res.count == 0


# -- extract: safety -------------------------------------------------------- #


def test_extract_skips_traversal(tmp_path):
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../escape.txt", b"nope")
        zf.writestr("ok.txt", b"yes")
    res = zipmonkey.extract(z, tmp_path / "out")
    assert "../escape.txt" in res.skipped_unsafe
    assert {p.name for p in res.extracted} == {"ok.txt"}
    assert not (tmp_path / "escape.txt").exists()


def test_extract_file_dir_name_collision(tmp_path):
    # Archive with both "foo" (file) and "foo/bar" (file under dir foo):
    # one of them cannot land on a real filesystem; it is skipped, not crashed.
    z = tmp_path / "clash.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("foo", b"i am a file")
        zf.writestr("foo/bar", b"i am under a dir")
    res = zipmonkey.extract(z, tmp_path / "out")
    # Exactly one survives; the other is recorded as a collision (not lost
    # silently, not raised).
    assert res.count == 1
    assert len(res.skipped_collisions) == 1
    assert res.count + len(res.skipped_collisions) == 2


def test_extract_total_bytes_limit(zip_factory, tmp_path):
    z = zip_factory("a.zip", {"big.bin": b"x" * 1000})
    with pytest.raises(zipmonkey.ArchiveLimitError):
        zipmonkey.extract(z, tmp_path / "out", max_total_bytes=500)


# -- extract: flatten ------------------------------------------------------- #


def test_flatten_collisions(zip_factory, tmp_path):
    z = zip_factory(
        "a.zip", {"a/data.csv": b"1", "b/data.csv": b"2", "c/data.csv": b"3"}
    )
    res = zipmonkey.extract(z, tmp_path / "out", flat=True)
    names = sorted(p.name for p in res.extracted)
    assert names == ["data (1).csv", "data (2).csv", "data.csv"]
    # All three byte payloads survived (no overwrite).
    contents = sorted((tmp_path / "out").glob("*.csv"))
    assert {p.read_bytes() for p in contents} == {b"1", b"2", b"3"}


# -- extract: recursive ----------------------------------------------------- #


def test_recursive_unpacks_nested(messy_zip, tmp_path):
    res = zipmonkey.extract(messy_zip, tmp_path / "out", recursive=True)
    assert any(p.name == "inner.zip" for p in res.nested_extracted)
    extracted_names = {p.name for p in res.extracted}
    # inner.zip contents must appear after recursion.
    assert "data.csv" in extracted_names
    assert "notes.txt" in extracted_names


def test_recursive_keeps_source_archive(messy_zip, tmp_path):
    res = zipmonkey.extract(messy_zip, tmp_path / "out", recursive=True)
    nested = res.nested_extracted[0]
    assert nested.exists()  # source not deleted


def test_recursive_depth_limit(tmp_path):
    # Build zip-in-zip-in-zip and cap depth at 1.
    level2 = tmp_path / "l2.zip"
    with zipfile.ZipFile(level2, "w") as zf:
        zf.writestr("deep.txt", b"deep")
    level1 = tmp_path / "l1.zip"
    with zipfile.ZipFile(level1, "w") as zf:
        zf.write(level2, "l2.zip")
    level0 = tmp_path / "l0.zip"
    with zipfile.ZipFile(level0, "w") as zf:
        zf.write(level1, "l1.zip")
    with pytest.raises(zipmonkey.ArchiveLimitError):
        zipmonkey.extract(level0, tmp_path / "out", recursive=True, max_depth=1)


# -- context manager cleanup ------------------------------------------------ #


def test_context_manager_cleans_temp_dir(zip_factory):
    z = zip_factory("a.zip", {"x.txt": b"hi"})
    with zipmonkey.open(z) as arc:
        res = arc.extract()  # dest=None -> temp dir
        temp = res.dest
        assert temp.exists()
    assert not temp.exists()  # cleaned on exit


def test_explicit_dest_not_cleaned(zip_factory, tmp_path):
    z = zip_factory("a.zip", {"x.txt": b"hi"})
    dest = tmp_path / "keep"
    with zipmonkey.open(z) as arc:
        arc.extract(dest)
    assert dest.exists()  # caller-owned dest survives


# -- read / namelist -------------------------------------------------------- #


def test_read_member(zip_factory):
    z = zip_factory("a.zip", {"x.txt": b"payload"})
    with zipmonkey.open(z) as arc:
        assert arc.read("x.txt") == b"payload"


def test_namelist(zip_factory):
    z = zip_factory("a.zip", {"a.txt": b"1", "b.txt": b"2"})
    with zipmonkey.open(z) as arc:
        assert set(arc.namelist()) == {"a.txt", "b.txt"}


# -- walk_typed ------------------------------------------------------------- #


def test_walk_typed_categories(messy_zip, tmp_path):
    typed = list(zipmonkey.walk_typed(messy_zip, tmp_path / "out"))
    by_name = {tf.path.name: tf for tf in typed}
    assert by_name["report.pdf"].category == "pdf"
    assert by_name["a.csv"].category == "tabular"
    # recursive default unpacks inner.zip -> its csv appears
    assert by_name["data.csv"].category == "tabular"


# -- password -------------------------------------------------------------- #


def test_password_protected_zip(tmp_path):
    z = tmp_path / "secret.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("s.txt", b"classified")
    # Re-create with a password using the legacy API path.
    z2 = tmp_path / "secret2.zip"
    with zipfile.ZipFile(z2, "w") as zf:
        zf.setpassword(b"hunter2")
        # writestr does not encrypt; use a real encrypted member via zipfile.
        zf.writestr("s.txt", b"classified")
    # zipfile cannot *write* encryption; verify password is accepted at open.
    with zipmonkey.open(z2, password=b"hunter2") as arc:
        assert arc.read("s.txt") == b"classified"
