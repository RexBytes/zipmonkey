"""Integration contract tests for Archive: open/inspect/extract/walk."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

import zipmonkey
from zipmonkey.archive import UnsupportedArchiveError

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
    # Skipped for the right reason: existing target, not a filter.
    assert res.skipped_existing == ["x.txt"]
    assert res.skipped_filtered == []


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


def _nested3(tmp_path):
    """Build l0.zip -> l1.zip -> l2.zip -> deep.txt and return l0."""
    level2 = tmp_path / "l2.zip"
    with zipfile.ZipFile(level2, "w") as zf:
        zf.writestr("deep.txt", b"deep")
    level1 = tmp_path / "l1.zip"
    with zipfile.ZipFile(level1, "w") as zf:
        zf.write(level2, "l2.zip")
    level0 = tmp_path / "l0.zip"
    with zipfile.ZipFile(level0, "w") as zf:
        zf.write(level1, "l1.zip")
    return level0


def test_recursive_depth_one_allows_single_nesting(tmp_path):
    # max_depth=1 must unpack exactly one level of nesting (no off-by-one).
    inner = tmp_path / "inner.zip"
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("leaf.txt", b"leaf")
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.write(inner, "inner.zip")
    res = zipmonkey.extract(outer, tmp_path / "out", recursive=True, max_depth=1)
    assert any(p.name == "leaf.txt" for p in res.extracted)
    assert res.skipped_nested == []


def test_recursive_depth_limit_records_not_raises(tmp_path):
    # Beyond max_depth: record the un-unpacked archive, do not raise/abort.
    level0 = _nested3(tmp_path)
    res = zipmonkey.extract(level0, tmp_path / "out", recursive=True, max_depth=1)
    # One level unpacked (l1 -> l2.zip written), l2.zip left as a container.
    assert len(res.skipped_nested) == 1
    assert res.skipped_nested[0].endswith("l2.zip")
    assert not any(p.name == "deep.txt" for p in res.extracted)


def test_recursive_depth_two_reaches_deeper(tmp_path):
    level0 = _nested3(tmp_path)
    res = zipmonkey.extract(level0, tmp_path / "out", recursive=True, max_depth=2)
    assert any(p.name == "deep.txt" for p in res.extracted)
    assert res.skipped_nested == []


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


def test_open_member_streams_in_chunks(zip_factory):
    z = zip_factory("a.zip", {"big.txt": b"abcdefghij" * 100})
    with zipmonkey.open(z) as arc:
        with arc.open_member("big.txt") as fh:
            collected = b""
            while True:
                chunk = fh.read(64)
                if not chunk:
                    break
                collected += chunk
    assert collected == b"abcdefghij" * 100


def test_extract_paths_are_absolute_for_relative_dest(tmp_path, monkeypatch):
    z = tmp_path / "a.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("sub/x.txt", b"1")
    monkeypatch.chdir(tmp_path)
    res = zipmonkey.extract(z, "relout")  # relative dest
    assert res.dest.is_absolute()
    assert all(p.is_absolute() for p in res.extracted)


def test_flat_extract_paths_absolute_for_relative_dest(tmp_path, monkeypatch):
    z = tmp_path / "a.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("sub/x.txt", b"1")
    monkeypatch.chdir(tmp_path)
    res = zipmonkey.extract(z, "relout", flat=True)
    assert all(p.is_absolute() for p in res.extracted)


def test_zip_symlink_marked_special_and_skipped(tmp_path):
    z = tmp_path / "links.zip"
    with zipfile.ZipFile(z, "w") as zf:
        info = zipfile.ZipInfo("alias.txt")
        info.external_attr = 0o120777 << 16  # S_IFLNK | 0777
        zf.writestr(info, "target.txt")  # body is the link target
        zf.writestr("target.txt", b"real")
    rep = zipmonkey.inspect(z)
    link = next(e for e in rep.entries if e.name == "alias.txt")
    assert link.is_special is True
    res = zipmonkey.extract(z, tmp_path / "out")
    assert res.skipped_links == ["alias.txt"]
    assert not (tmp_path / "out" / "alias.txt").exists()
    assert (tmp_path / "out" / "target.txt").read_bytes() == b"real"


def test_open_member_directory_returns_empty_stream(tmp_path):
    z = tmp_path / "d.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("d/", b"")
        zf.writestr("d/f.txt", b"hi")
    with zipmonkey.open(z) as arc:
        with arc.open_member("d/") as fh:
            assert fh.read() == b""


# -- walk_typed ------------------------------------------------------------- #


def test_walk_typed_categories(messy_zip, tmp_path):
    typed = list(zipmonkey.walk_typed(messy_zip, tmp_path / "out"))
    by_name = {tf.path.name: tf for tf in typed}
    assert by_name["report.pdf"].category == "pdf"
    assert by_name["a.csv"].category == "tabular"
    # recursive default unpacks inner.zip -> its csv appears
    assert by_name["data.csv"].category == "tabular"


# -- password -------------------------------------------------------------- #


def test_read_directory_member_returns_empty(tmp_path):
    z = tmp_path / "d.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("d/", b"")
        zf.writestr("d/f.txt", b"hi")
    with zipmonkey.open(z) as arc:
        assert arc.read("d/") == b""


# -- bomb / fan-out guards (streaming) -------------------------------------- #


def test_byte_limit_enforced_mid_stream(tmp_path):
    # A single member larger than the cap must trip the limit while streaming
    # (proving the whole member is not buffered before the check).
    z = tmp_path / "big.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.bin", b"\x00" * (4 * 1024 * 1024))  # compresses tiny
    with pytest.raises(zipmonkey.ArchiveLimitError) as exc:
        zipmonkey.extract(z, tmp_path / "out", max_total_bytes=1024 * 1024)
    # Partial result attached for cleanup/reporting.
    assert exc.value.partial_result is not None
    assert exc.value.partial_result.dest == tmp_path / "out"
    # The half-written target was removed, not left truncated.
    assert not (tmp_path / "out" / "big.bin").exists()


def test_max_member_bytes_rejects_oversized(tmp_path):
    z = tmp_path / "big.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("small.txt", b"x" * 10)
        zf.writestr("big.txt", b"y" * 100)
    out = tmp_path / "out"
    with pytest.raises(zipmonkey.ArchiveLimitError) as exc:
        zipmonkey.extract(z, out, max_member_bytes=50)
    # The oversized member is rejected before writing.
    assert not (out / "big.txt").exists()
    assert exc.value.partial_result is not None


def test_max_member_bytes_zero_disables(tmp_path):
    z = tmp_path / "ok.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("big.txt", b"y" * 100)
    res = zipmonkey.extract(z, tmp_path / "out", max_member_bytes=0)
    assert res.count == 1


def test_file_count_limit_enforced(tmp_path):
    z = tmp_path / "many.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for i in range(5):
            zf.writestr(f"f{i}.txt", b"x")
    with pytest.raises(zipmonkey.ArchiveLimitError) as exc:
        zipmonkey.extract(z, tmp_path / "out", max_files=3)
    assert exc.value.partial_result is not None


def test_file_count_limit_preflight_does_not_write_over_limit(tmp_path):
    # The over-limit member must NOT be written to disk (preflight, not
    # write-then-fail), and the partial result must match what is on disk.
    z = tmp_path / "two.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("a.txt", b"a")
        zf.writestr("b.txt", b"b")
    out = tmp_path / "out"
    with pytest.raises(zipmonkey.ArchiveLimitError) as exc:
        zipmonkey.extract(z, out, max_files=1)
    on_disk = sorted(p.name for p in out.iterdir())
    assert on_disk == ["a.txt"]  # b.txt never written
    assert [p.name for p in exc.value.partial_result.extracted] == ["a.txt"]


# -- include + recursive reaches inside nested archives (H1) ---------------- #


def test_include_filter_still_reaches_nested(tmp_path):
    inner = tmp_path / "inner.zip"
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("deep.csv", b"a,b\n")
        zf.writestr("deep.log", b"noise")
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.write(inner, "nested/inner.zip")
        zf.writestr("top.csv", b"x\n")
        zf.writestr("top.log", b"noise")
    res = zipmonkey.extract(
        outer, tmp_path / "out", include="*.csv", recursive=True
    )
    names = {p.name for p in res.extracted}
    assert names == {"top.csv", "deep.csv"}  # reached inside inner.zip
    assert any(p.name == "inner.zip" for p in res.nested_extracted)


# -- flat mode collision safety (C2) and recursion propagation -------------- #


def test_flat_does_not_clobber_existing_disk_file(tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "data.csv").write_bytes(b"old")
    z = tmp_path / "a.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("sub/data.csv", b"new")
    res = zipmonkey.extract(z, dest, flat=True)
    # Pre-existing file preserved; archive member written under a fresh name.
    assert (dest / "data.csv").read_bytes() == b"old"
    assert (dest / "data (1).csv").read_bytes() == b"new"
    assert res.count == 1


def test_flat_recursive_flattens_nested(tmp_path):
    inner = tmp_path / "inner.zip"
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("deep/a.txt", b"A")
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.write(inner, "nested/inner.zip")
        zf.writestr("sub/top.txt", b"T")
    dest = tmp_path / "out"
    res = zipmonkey.extract(outer, dest, flat=True, recursive=True)
    leaf_names = {p.name for p in res.extracted}
    assert leaf_names == {"top.txt", "a.txt"}
    assert (dest / "a.txt").read_bytes() == b"A"


def test_extracted_dir_collides_with_member(tmp_path):
    # An archive member literally named "<archive>_extracted" must not crash.
    inner = tmp_path / "x.zip"
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("leaf.txt", b"L")
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.write(inner, "x.zip")
        zf.writestr("x.zip_extracted", b"i am a file")
    res = zipmonkey.extract(outer, tmp_path / "out", recursive=True)  # no raise
    assert any(p.name == "leaf.txt" for p in res.extracted)


# -- exclude-only filter ---------------------------------------------------- #


def test_exclude_only(zip_factory, tmp_path):
    z = zip_factory(
        "a.zip", {"keep.txt": b"1", "drop.log": b"2", "also.txt": b"3"}
    )
    res = zipmonkey.extract(z, tmp_path / "out", exclude="*.log")
    assert {p.name for p in res.extracted} == {"keep.txt", "also.txt"}
    assert "drop.log" in res.skipped_filtered


# -- tar special members (symlinks) ---------------------------------------- #


def _tar_with_symlink(path):
    with tarfile.open(path, "w") as tf:
        data = b"real content"
        ti = tarfile.TarInfo("target.txt")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
        link = tarfile.TarInfo("alias.txt")
        link.type = tarfile.SYMTYPE
        link.linkname = "target.txt"
        tf.addfile(link)
    return path


def test_tar_symlink_skipped(tmp_path):
    t = _tar_with_symlink(tmp_path / "links.tar")
    res = zipmonkey.extract(t, tmp_path / "out")
    assert res.skipped_links == ["alias.txt"]
    assert (tmp_path / "out" / "target.txt").read_bytes() == b"real content"
    assert not (tmp_path / "out" / "alias.txt").exists()


def test_tar_symlink_marked_special_in_inspect(tmp_path):
    t = _tar_with_symlink(tmp_path / "links.tar")
    rep = zipmonkey.inspect(t)
    link = next(e for e in rep.entries if e.name == "alias.txt")
    assert link.is_special is True
    target = next(e for e in rep.entries if e.name == "target.txt")
    assert target.is_special is False


def test_tar_hardlink_reads_as_empty(tmp_path):
    # read()/open_member must honour the "special members are empty" contract
    # rather than resolving a hardlink to its target's bytes.
    t = tmp_path / "hard.tar"
    with tarfile.open(t, "w") as tf:
        data = b"secret"
        ti = tarfile.TarInfo("real.txt")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
        ln = tarfile.TarInfo("hard.txt")
        ln.type = tarfile.LNKTYPE
        ln.linkname = "real.txt"
        tf.addfile(ln)
    with zipmonkey.open(t) as arc:
        assert arc.read("hard.txt") == b""
        with arc.open_member("hard.txt") as fh:
            assert fh.read() == b""
        assert arc.read("real.txt") == b"secret"
    rep = zipmonkey.inspect(t)
    hard = next(e for e in rep.entries if e.name == "hard.txt")
    assert hard.is_special is True
    # And extraction skips it.
    res = zipmonkey.extract(t, tmp_path / "out")
    assert "hard.txt" in res.skipped_links


def test_tar_with_unresolvable_symlink_does_not_crash(tmp_path):
    t = tmp_path / "bad.tar"
    with tarfile.open(t, "w") as tf:
        link = tarfile.TarInfo("alias.txt")
        link.type = tarfile.SYMTYPE
        link.linkname = "/nonexistent/outside"
        tf.addfile(link)
        data = b"ok"
        ti = tarfile.TarInfo("real.txt")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
    res = zipmonkey.extract(t, tmp_path / "out")  # must not raise KeyError
    assert (tmp_path / "out" / "real.txt").read_bytes() == b"ok"
    assert "alias.txt" in res.skipped_links


# -- corrupt streams raise the documented exception (H4) -------------------- #


def test_corrupt_gzip_raises_unsupported(tmp_path):
    bad = tmp_path / "bad.gz"
    bad.write_bytes(b"\x1f\x8b\x08\x00" + b"\x00" * 8 + b"not valid deflate")
    with pytest.raises(zipmonkey.UnsupportedArchiveError):
        zipmonkey.open(bad)


def test_corrupt_zip_raises_unsupported(tmp_path):
    # ZIP magic but not a valid zip must surface as UnsupportedArchiveError,
    # not a raw zipfile.BadZipFile.
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"PK\x03\x04 then total garbage, not a real central dir")
    with pytest.raises(zipmonkey.UnsupportedArchiveError):
        zipmonkey.open(bad)


def test_recursive_does_not_unpack_xlsx(tmp_path):
    # An .xlsx (a zip container) inside an archive must be treated as an Excel
    # leaf, not unpacked into its XML parts.
    xlsx = tmp_path / "report.xlsx"
    with zipfile.ZipFile(xlsx, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("xl/workbook.xml", "<workbook/>")
    outer = tmp_path / "bundle.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.write(xlsx, "report.xlsx")
    out = tmp_path / "out"
    res = zipmonkey.extract(outer, out, recursive=True)
    assert [p.name for p in res.extracted] == ["report.xlsx"]
    assert res.nested_extracted == []
    assert not (out / "report.xlsx_extracted").exists()


def test_walk_typed_xlsx_is_excel_leaf(tmp_path):
    xlsx = tmp_path / "report.xlsx"
    with zipfile.ZipFile(xlsx, "w") as zf:
        zf.writestr("xl/workbook.xml", "<workbook/>")
    outer = tmp_path / "bundle.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.write(xlsx, "report.xlsx")
    typed = list(zipmonkey.walk_typed(outer, tmp_path / "out"))
    assert [(t.path.name, t.detected_type, t.category) for t in typed] == [
        ("report.xlsx", "xlsx", "excel")
    ]


def test_recursive_does_not_unpack_jar(tmp_path):
    jar = tmp_path / "app.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
    outer = tmp_path / "bundle.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.write(jar, "app.jar")
    res = zipmonkey.extract(outer, tmp_path / "out", recursive=True)
    assert [p.name for p in res.extracted] == ["app.jar"]
    assert res.nested_extracted == []


def test_recursive_invalid_archive_magic_kept_as_leaf(tmp_path):
    # A member with archive magic that is NOT a valid archive must be reported
    # as an extracted leaf, never as nested_extracted (it was not unpacked).
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.writestr("fake.zip", b"PK\x03\x04 not actually a zip")
        zf.writestr("real.txt", b"hi")
    res = zipmonkey.extract(outer, tmp_path / "out", recursive=True)
    names = {p.name for p in res.extracted}
    assert "fake.zip" in names  # kept as a leaf
    assert "real.txt" in names
    assert res.nested_extracted == []  # nothing was actually unpacked


def test_written_count_includes_containers(tmp_path):
    inner = tmp_path / "inner.zip"
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("a.txt", b"A")
        zf.writestr("b.txt", b"B")
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.write(inner, "inner.zip")
    res = zipmonkey.extract(outer, tmp_path / "out", recursive=True)
    assert res.count == 2  # leaves a.txt, b.txt
    assert len(res.nested_extracted) == 1  # inner.zip container
    assert res.written_count == 3  # what max_files counts


def test_depth_skipped_not_double_listed(tmp_path):
    # An over-depth archive belongs in skipped_nested only, not nested_extracted.
    level0 = _nested3(tmp_path)
    res = zipmonkey.extract(level0, tmp_path / "out", recursive=True, max_depth=1)
    assert len(res.skipped_nested) == 1
    assert not any(str(p).endswith("l2.zip") for p in res.nested_extracted)


# -- single-file backends (bz2/xz) and fallback name ------------------------ #


@pytest.mark.parametrize(
    "ext,opener,fmt",
    [
        (".csv.bz2", "bz2", "bzip2"),
        (".csv.xz", "lzma", "xz"),
    ],
)
def test_single_compressed_file(tmp_path, ext, opener, fmt):
    import bz2
    import lzma

    p = tmp_path / ("lone" + ext)
    mod = {"bz2": bz2, "lzma": lzma}[opener]
    with mod.open(p, "wb") as f:
        f.write(b"a,b\n1,2\n")
    rep = zipmonkey.inspect(p)
    assert rep.format == fmt
    assert rep.entries[0].name == "lone.csv"
    with zipmonkey.open(p) as arc:
        assert arc.read("lone.csv") == b"a,b\n1,2\n"


def test_single_file_fallback_name(tmp_path):
    import gzip

    # A file named exactly ".gz" strips to empty -> fallback member "data".
    p = tmp_path / ".gz"
    with gzip.open(p, "wb") as f:
        f.write(b"payload")
    rep = zipmonkey.inspect(p)
    assert rep.entries[0].name == "data"


# -- walk_typed recursive flag --------------------------------------------- #


def test_walk_typed_non_recursive_yields_archive(messy_zip, tmp_path):
    typed = list(zipmonkey.walk_typed(messy_zip, tmp_path / "out", recursive=False))
    by_name = {tf.path.name: tf for tf in typed}
    # Nested archive itself is yielded as category "archive"; its contents are
    # NOT present (not unpacked).
    assert by_name["inner.zip"].category == "archive"
    assert "data.csv" not in by_name  # the file inside inner.zip


def test_walk_typed_recursive_excludes_container(messy_zip, tmp_path):
    typed = list(zipmonkey.walk_typed(messy_zip, tmp_path / "out", recursive=True))
    names = {tf.path.name for tf in typed}
    # Recursive: leaves present, the nested container is not re-yielded.
    assert "data.csv" in names
    assert "inner.zip" not in names


def _make_encrypted_zip(tmp_path) -> Path:
    """Create a real ZipCrypto-encrypted zip via the `zip` CLI, or skip."""
    import shutil
    import subprocess

    if shutil.which("zip") is None:  # pragma: no cover - env dependent
        pytest.skip("`zip` CLI not available to build an encrypted fixture")
    plain = tmp_path / "s.txt"
    plain.write_bytes(b"classified")
    enc = tmp_path / "secret.zip"
    subprocess.run(
        ["zip", "-j", "-P", "hunter2", str(enc), str(plain)],
        check=True,
        capture_output=True,
    )
    return enc


def test_password_correct_decrypts(tmp_path):
    enc = _make_encrypted_zip(tmp_path)
    with zipmonkey.open(enc, password=b"hunter2") as arc:
        assert arc.read("s.txt") == b"classified"


def test_password_wrong_raises(tmp_path):
    enc = _make_encrypted_zip(tmp_path)
    with zipmonkey.open(enc, password=b"WRONG") as arc:
        # Normalised to ArchiveReadError (not a raw zipfile RuntimeError).
        with pytest.raises(zipmonkey.ArchiveReadError):
            arc.read("s.txt")


def test_password_missing_raises(tmp_path):
    enc = _make_encrypted_zip(tmp_path)
    with zipmonkey.open(enc) as arc:
        with pytest.raises(zipmonkey.ArchiveReadError):
            arc.read("s.txt")


def test_password_wrong_inspect_raises_read_error(tmp_path):
    enc = _make_encrypted_zip(tmp_path)
    with zipmonkey.open(enc, password=b"WRONG") as arc:
        with pytest.raises(zipmonkey.ArchiveReadError):
            arc.inspect()  # peek() per member hits the bad password


def test_password_wrong_extract_raises_read_error(tmp_path):
    enc = _make_encrypted_zip(tmp_path)
    with pytest.raises(zipmonkey.ArchiveReadError):
        zipmonkey.extract(enc, tmp_path / "out", password=b"WRONG")


def test_nested_archive_inherits_password(tmp_path):
    # An encrypted inner zip embedded in an outer zip must decrypt during
    # recursive extraction using the top-level password.
    inner_enc = _make_encrypted_zip(tmp_path)  # secret.zip with s.txt
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.write(inner_enc, "nested/secret.zip")
    res = zipmonkey.extract(
        outer, tmp_path / "out", password=b"hunter2", recursive=True
    )
    leaf = next(p for p in res.extracted if p.name == "s.txt")
    assert leaf.read_bytes() == b"classified"
