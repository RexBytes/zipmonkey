"""Tests for the optional 7z / rar backends.

These are gated behind ``importorskip`` so they run only where the optional
dependency is installed, and exercise the same contract as the stdlib
backends: format label, entry shape, and a read round-trip. A separate test
pins the "missing dependency" error path, which can run anywhere the lib is
absent.
"""

from __future__ import annotations

import io
import zipfile

import pytest

import zipmonkey
from zipmonkey.archive import (
    UnsupportedArchiveError,
    _RarBackend,
    _SevenZipBackend,
    _SingleFileBackend,
    _TarBackend,
    _ZipBackend,
)
from zipmonkey.models import ArchiveEntry

# -- backend streaming contract (no optional deps required) ----------------- #


def test_streaming_flags():
    # The stdlib backends stream; 7z does not (py7zr has no streaming API).
    assert _ZipBackend.streaming is True
    assert _TarBackend.streaming is True
    assert _SingleFileBackend.streaming is True
    assert _RarBackend.streaming is True
    assert _SevenZipBackend.streaming is False


class _StubNonStreaming:
    """A non-streaming backend declaring a huge member but yielding tiny bytes.

    Mimics a 7z bomb whose header honestly declares a large uncompressed size:
    the extraction preflight must reject it *before* open_stream materialises.
    """

    format = "stub"
    streaming = False

    def __init__(self):
        self.opened = False
        self.peeked = False

    def entries(self):
        return [
            ArchiveEntry(
                name="big.bin",
                size=10_000_000,
                compressed_size=10,
                is_dir=False,
                is_artifact=False,
            )
        ]

    def read(self, name):  # pragma: no cover - not reached
        return b"x" * 10

    def peek(self, name, n):
        self.peeked = True  # for 7z this would materialise the whole member
        return b"x" * n

    def open_stream(self, name):
        self.opened = True  # must NOT happen when preflight rejects
        return io.BytesIO(b"x" * 10)

    def close(self):
        pass


def test_non_streaming_preflight_rejects_before_materialising(tmp_path):
    z = tmp_path / "tiny.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("placeholder.txt", b"x")
    with zipmonkey.open(z) as arc:
        stub = _StubNonStreaming()
        arc._backend = stub  # swap in the non-streaming bomb
        with pytest.raises(zipmonkey.ArchiveLimitError):
            arc.extract(tmp_path / "out", max_total_bytes=1000)
        assert stub.opened is False  # rejected before decompression
    assert not (tmp_path / "out" / "big.bin").exists()


def test_max_member_bytes_rejects_before_materialising(tmp_path):
    # The per-member cap must reject a big non-streaming member before its
    # open_stream (which would decompress into memory) is ever called.
    z = tmp_path / "tiny.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("placeholder.txt", b"x")
    with zipmonkey.open(z) as arc:
        stub = _StubNonStreaming()  # declares a 10 MB member
        arc._backend = stub
        with pytest.raises(zipmonkey.ArchiveLimitError):
            arc.extract(tmp_path / "out", max_member_bytes=1024)
        assert stub.opened is False


def test_non_streaming_recursive_cap_rejects_before_peek(tmp_path):
    # Under recursion the archive-sniff peek() materialises a non-streaming
    # member; the per-member cap must reject an oversized member BEFORE peek.
    z = tmp_path / "tiny.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("placeholder.txt", b"x")
    with zipmonkey.open(z) as arc:
        stub = _StubNonStreaming()  # declares a 10 MB member
        arc._backend = stub
        with pytest.raises(zipmonkey.ArchiveLimitError):
            arc.extract(tmp_path / "out", recursive=True, max_member_bytes=1024)
        assert stub.peeked is False  # rejected before the materialising peek
        assert stub.opened is False


def test_rar_special_detection_logic():
    # _rar_special is a pure attribute probe; exercise it without rarfile.
    from zipmonkey.archive import _RarBackend

    class Redir:  # RAR5-style symlink/redirect record
        file_redir = ("SYMLINK", 0, "target")

    class SymMethod:  # older rarfile exposes is_symlink()
        file_redir = None

        def is_symlink(self):
            return True

    class Plain:
        file_redir = None

        def is_symlink(self):
            return False

    assert _RarBackend._rar_special(Redir()) is True
    assert _RarBackend._rar_special(SymMethod()) is True
    assert _RarBackend._rar_special(Plain()) is False
    assert _RarBackend._rar_special(object()) is False  # no metadata at all


def test_sevenzip_roundtrip(tmp_path):
    py7zr = pytest.importorskip("py7zr")
    archive = tmp_path / "a.7z"
    with py7zr.SevenZipFile(archive, "w") as zf:
        src = tmp_path / "data.csv"
        src.write_bytes(b"a,b\n1,2\n")
        zf.write(src, "data.csv")
    rep = zipmonkey.inspect(archive)
    assert rep.format == "7z"
    assert any(e.name == "data.csv" for e in rep.entries)
    with zipmonkey.open(archive) as arc:
        assert arc.read("data.csv") == b"a,b\n1,2\n"


def _make_7z(tmp_path, members: dict[str, bytes]):
    py7zr = pytest.importorskip("py7zr")
    archive = tmp_path / "a.7z"
    with py7zr.SevenZipFile(archive, "w") as zf:
        for name, data in members.items():
            src = tmp_path / name.replace("/", "_")
            src.write_bytes(data)
            zf.write(src, name)
    return archive


def test_sevenzip_read_nested_member(tmp_path):
    # Regression: py7zr >= 1.0 removed SevenZipFile.read(); reading a member
    # (including one under a subdirectory) must still return its exact bytes
    # across the 0.x/1.x API boundary.
    archive = _make_7z(tmp_path, {"top.txt": b"hello", "sub/inner.csv": b"a,b\n"})
    with zipmonkey.open(archive) as arc:
        assert arc.read("top.txt") == b"hello"
        assert arc.read("sub/inner.csv") == b"a,b\n"


def test_sevenzip_extract(tmp_path):
    archive = _make_7z(tmp_path, {"a.csv": b"x,y\n1,2\n", "b.txt": b"hello"})
    res = zipmonkey.extract(archive, tmp_path / "out")
    assert res.count == 2
    assert (tmp_path / "out" / "a.csv").read_bytes() == b"x,y\n1,2\n"


def test_sevenzip_include_filter(tmp_path):
    archive = _make_7z(tmp_path, {"a.csv": b"1", "b.log": b"2"})
    res = zipmonkey.extract(archive, tmp_path / "out", include="*.csv")
    assert {p.name for p in res.extracted} == {"a.csv"}


def test_sevenzip_symlink_flagged_and_skipped(tmp_path):
    # py7zr stores symlinks and exposes FileInfo.is_symlink; the 7z backend must
    # flag them is_special and skip them on extraction, exactly like ZIP/tar —
    # not materialise them as empty regular files (which inflated count and left
    # skipped_links empty before this fix).
    py7zr = pytest.importorskip("py7zr")
    real = tmp_path / "r.txt"
    real.write_bytes(b"content")
    link = tmp_path / "s.txt"
    link.symlink_to("r.txt")
    archive = tmp_path / "a.7z"
    with py7zr.SevenZipFile(archive, "w") as zf:
        zf.write(real, "r.txt")
        zf.write(link, "s.txt")

    rep = zipmonkey.inspect(archive)
    sym = next(e for e in rep.entries if e.name == "s.txt")
    assert sym.is_special is True

    res = zipmonkey.extract(archive, tmp_path / "out")
    assert res.skipped_links == ["s.txt"]
    assert res.count == 1  # only the real file is a leaf
    assert {p.name for p in (tmp_path / "out").iterdir()} == {"r.txt"}
    # A special member reads as empty, symmetric with tar/zip.
    with zipmonkey.open(archive) as arc:
        assert arc.read("s.txt") == b""


def test_sevenzip_encrypted_header_missing_password_names_cause(tmp_path):
    # An encrypted-header 7z needs the password just to LIST members, so a
    # missing password surfaces at open time. It must name the password cause,
    # not be folded into the generic "corrupt or unsupported" message that is
    # indistinguishable from a genuinely bad file.
    py7zr = pytest.importorskip("py7zr")
    enc = tmp_path / "enc.7z"
    with py7zr.SevenZipFile(enc, "w", password="secret") as zf:
        zf.set_encrypted_header(True)
        zf.writestr(b"hello", "f.txt")

    with pytest.raises(UnsupportedArchiveError, match="password"):
        zipmonkey.open(enc)
    # The correct password still opens and reads it.
    with zipmonkey.open(enc, password=b"secret") as arc:
        assert arc.read("f.txt") == b"hello"


def test_sevenzip_missing_member_raises(tmp_path):
    archive = _make_7z(tmp_path, {"a.txt": b"hi"})
    with zipmonkey.open(archive) as arc:
        with pytest.raises(zipmonkey.ArchiveReadError):
            arc.read("nope.txt")


def test_sevenzip_duplicate_member_name_read_one_extract_records_collision(tmp_path):
    # Documented limitation: read-by-name returns one member for a duplicated
    # name (no key disambiguates them); extraction preserves both -- first wins,
    # the later duplicate is recorded in skipped_collisions (nothing is lost).
    py7zr = pytest.importorskip("py7zr")
    archive = tmp_path / "dup.7z"
    with py7zr.SevenZipFile(archive, "w") as zf:
        zf.writef(io.BytesIO(b"ALICE"), "f.txt")
        zf.writef(io.BytesIO(b"BOB"), "f.txt")
    with zipmonkey.open(archive) as arc:
        assert len(arc.entries()) == 2
        assert arc.read("f.txt") == b"ALICE"  # by-name resolves to the first
    res = zipmonkey.extract(archive, tmp_path / "out")
    assert res.count == 1
    assert res.skipped_collisions == ["f.txt"]


def test_sevenzip_recursive_member_cap_precedes_filter(tmp_path):
    # Documented limitation: under recursion the non-streaming 7z backend
    # materialises each member to sniff it by CONTENT (for nested-archive
    # detection), so max_member_bytes is enforced before the leaf filter. An
    # over-cap member that would be filtered still raises -- whereas the
    # streaming ZIP backend peeks cheaply and filters it first. (An earlier
    # extension-based sniff that dodged this was reverted; see LIMITATIONS.md.)
    py7zr = pytest.importorskip("py7zr")
    archive = tmp_path / "a.7z"
    with py7zr.SevenZipFile(archive, "w") as zf:
        zf.writef(io.BytesIO(b"\x00" * 1000), "big.bin")
        zf.writef(io.BytesIO(b"x,y\n"), "keep.csv")
    # non-recursive: no sniff, so the filter drops big.bin cleanly
    res = zipmonkey.extract(
        archive, tmp_path / "nr", include="*.csv", max_member_bytes=500
    )
    assert res.skipped_filtered == ["big.bin"]
    # recursive: the per-member cap fires before the filter (consistent for any
    # member name, archive-like or not -- no name-based asymmetry).
    for name in ("big.bin", "big.zip"):
        a2 = tmp_path / f"{name}.7z"
        with py7zr.SevenZipFile(a2, "w") as zf:
            zf.writef(io.BytesIO(b"\x00" * 1000), name)
            zf.writef(io.BytesIO(b"x,y\n"), "keep.csv")
        with pytest.raises(zipmonkey.ArchiveLimitError):
            zipmonkey.extract(
                a2,
                tmp_path / f"r_{name}",
                recursive=True,
                include="*.csv",
                max_member_bytes=500,
            )


def test_sevenzip_recursive_nesting_detected_by_content(tmp_path):
    # Nested-archive detection is by CONTENT (magic bytes), so a real archive is
    # recursed regardless of its name -- both "inner.7z" and a real archive
    # stored as "renamed.bin" are unpacked; a plain file is a leaf.
    py7zr = pytest.importorskip("py7zr")
    inner = tmp_path / "inner.7z"
    with py7zr.SevenZipFile(inner, "w") as zf:
        zf.writef(io.BytesIO(b"LEAF"), "leaf.txt")
    outer = tmp_path / "outer.7z"
    with py7zr.SevenZipFile(outer, "w") as zf:
        zf.write(inner, "inner.7z")           # real archive, archive name
        zf.write(inner, "renamed.bin")        # real archive, non-archive name
        zf.writef(io.BytesIO(b"plain"), "note.txt")  # genuine leaf
    res = zipmonkey.extract(outer, tmp_path / "out", recursive=True)
    assert {p.name for p in res.nested_extracted} == {"inner.7z", "renamed.bin"}
    assert "note.txt" in {p.name for p in res.extracted}


def test_sevenzip_read_keyed_by_member_name_no_path_reconstruction(tmp_path):
    # The 7z read path captures each member into memory keyed by py7zr's own
    # member name (no temp-file path reconstruction). Pin the mechanism over a
    # spread of adversarial names -- interior "..", dot-prefixed basenames,
    # nested dirs, unicode, empty -- each must round-trip its exact bytes. The
    # two historical silent-data-loss defects both lived in the old
    # reconstruct-Path(td)/name strategy this replaced.
    py7zr = pytest.importorskip("py7zr")
    import io

    cases = {
        "docs/../report.txt": b"R" * 42,
        "..notes.txt": b"DOTDOT",
        "...x": b"TRIPLE",
        "a/b/c/deep.bin": b"NESTED" * 100,
        "uniéñ.txt": b"UNICODE",
        "empty.txt": b"",
    }
    archive = tmp_path / "a.7z"
    with py7zr.SevenZipFile(archive, "w") as zf:
        for name, data in cases.items():
            zf.writef(io.BytesIO(data), name)

    with zipmonkey.open(archive) as arc:
        for name, data in cases.items():
            assert arc.read(name) == data, name
    # And extraction writes the true bytes, not silently-empty files.
    zipmonkey.extract(archive, tmp_path / "out")
    on_disk = {
        p.read_bytes() for p in (tmp_path / "out").rglob("*") if p.is_file()
    }
    assert {d for d in cases.values() if d} <= on_disk


@pytest.mark.parametrize("dotname", ["..notes.txt", "...txt", "..foo"])
def test_sevenzip_dotdot_prefixed_basename_not_lost(tmp_path, dotname):
    # The interior-".." escape guard must reject only a genuine leading ".."
    # path component, NOT a legitimate basename that merely begins with two
    # dots -- those used to read as b"" (silent data loss), the same class the
    # guard was added to prevent.
    py7zr = pytest.importorskip("py7zr")
    import io

    payload = b"REAL_USER_DATA_" + dotname.encode()
    archive = tmp_path / "a.7z"
    with py7zr.SevenZipFile(archive, "w") as zf:
        zf.writef(io.BytesIO(payload), dotname)
    with zipmonkey.open(archive) as arc:
        assert arc.read(dotname) == payload
    zipmonkey.extract(archive, tmp_path / "out")
    written = [p for p in (tmp_path / "out").rglob("*") if p.is_file()]
    assert [p.read_bytes() for p in written] == [payload]


def test_sevenzip_interior_dotdot_member_reads_full_content(tmp_path):
    # py7zr writes a member to its NORMALISED path, so a member stored as
    # "docs/../report.txt" lands at "report.txt". Reconstructing the raw name
    # used to look in a directory that was never created and silently return
    # b"" -- extracting a 0-byte file (silent data loss). The full bytes must
    # come back, and the extracted file must match the declared size.
    py7zr = pytest.importorskip("py7zr")
    payload = b"X" * 42
    src = tmp_path / "report.txt"
    src.write_bytes(payload)
    archive = tmp_path / "a.7z"
    with py7zr.SevenZipFile(archive, "w") as zf:
        zf.write(src, "docs/../report.txt")

    with zipmonkey.open(archive) as arc:
        assert arc.read("docs/../report.txt") == payload
    res = zipmonkey.extract(archive, tmp_path / "out")
    assert res.skipped_unsafe == []  # re-roots in-bounds, not unsafe
    written = [p for p in (tmp_path / "out").rglob("*") if p.is_file()]
    assert [p.read_bytes() for p in written] == [payload]


def test_sevenzip_directory_open_stream_returns_none(tmp_path):
    # The _Backend.open_stream contract returns None for directory members.
    py7zr = pytest.importorskip("py7zr")
    d = tmp_path / "src" / "sub"
    d.mkdir(parents=True)
    archive = tmp_path / "a.7z"
    with py7zr.SevenZipFile(archive, "w") as zf:
        zf.write(tmp_path / "src" / "sub", "sub")
    with zipmonkey.open(archive) as arc:
        assert arc._backend.open_stream("sub") is None


def test_sevenzip_byte_cap_preflight(tmp_path):
    archive = _make_7z(tmp_path, {"big.bin": b"\x00" * (2 * 1024 * 1024)})
    # Declared uncompressed size (2 MiB) exceeds the cap -> rejected.
    with pytest.raises(zipmonkey.ArchiveLimitError):
        zipmonkey.extract(archive, tmp_path / "out", max_total_bytes=64 * 1024)


def test_rar_roundtrip(tmp_path):
    pytest.importorskip("rarfile")
    pytest.skip("creating .rar fixtures requires the proprietary `rar` binary")


def test_sevenzip_missing_dep_raises(tmp_path, monkeypatch):
    # Simulate py7zr being absent: constructing the backend must raise the
    # documented UnsupportedArchiveError with the install hint.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "py7zr":
            raise ImportError("no py7zr")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(UnsupportedArchiveError, match="py7zr"):
        _SevenZipBackend(tmp_path / "x.7z", None)


def test_rar_missing_dep_raises(tmp_path, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "rarfile":
            raise ImportError("no rarfile")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(UnsupportedArchiveError, match="rarfile"):
        _RarBackend(tmp_path / "x.rar", None)


def test_sevenzip_recursive_non_archive_decoy_name_honours_filter(tmp_path):
    # Regression: the 7z extension-based nested detection presumes a member named
    # like an archive is a container, so it bypassed the leaf filter. A plain
    # file named "decoy.zip" failed to open as an archive and was extracted as a
    # leaf even when a filter excluded it. It must now be filtered (and removed),
    # matching the content-sniffing ZIP/tar backends -- while a REAL nested 7z is
    # still always traversed.
    py7zr = pytest.importorskip("py7zr")
    archive = tmp_path / "a.7z"
    with py7zr.SevenZipFile(archive, "w") as zf:
        zf.writef(io.BytesIO(b"plain text, not a zip"), "decoy.zip")
        zf.writef(io.BytesIO(b"hello"), "wanted.txt")

    res = zipmonkey.extract(
        archive, tmp_path / "inc", recursive=True, include="*.txt"
    )
    assert [p.name for p in res.extracted] == ["wanted.txt"]
    assert res.skipped_filtered == ["decoy.zip"]
    assert not (tmp_path / "inc" / "decoy.zip").exists()  # removed, not left behind
    assert res.written_count == 1  # accounting unwound

    # exclude direction too
    res2 = zipmonkey.extract(
        archive, tmp_path / "exc", recursive=True, exclude="*.zip"
    )
    assert res2.skipped_filtered == ["decoy.zip"]

    # No filter: the decoy is still kept as a leaf (unchanged behaviour).
    res3 = zipmonkey.extract(archive, tmp_path / "all", recursive=True)
    assert {p.name for p in res3.extracted} == {"decoy.zip", "wanted.txt"}


def test_sevenzip_flat_decoy_releases_basename_reservation(tmp_path):
    # Regression (sibling of the Panel-9 decoy fix): in flat mode, a filtered-out
    # decoy (extension-classified container that turns out to be a leaf) must
    # release its flat-mode basename reservation when unwritten -- otherwise a
    # later legitimately-named member is spuriously renamed "name (1)" even
    # though the basename is free on disk.
    py7zr = pytest.importorskip("py7zr")
    import zipfile

    realzip = tmp_path / "real.zip"
    with zipfile.ZipFile(realzip, "w") as zf:
        zf.writestr("payload.txt", b"PAYLOAD")
    archive = tmp_path / "o.7z"
    with py7zr.SevenZipFile(archive, "w") as zf:
        zf.writef(io.BytesIO(b"plain text decoy"), "dirA/decoy.zip")  # text, filtered
        zf.write(realzip, "dirB/decoy.zip")  # real zip, same basename

    res = zipmonkey.extract(
        archive, tmp_path / "out", flat=True, recursive=True, exclude="*.zip"
    )
    assert res.skipped_filtered == ["dirA/decoy.zip"]
    # The real nested archive keeps the freed basename, not "decoy (1).zip".
    assert {p.name for p in (tmp_path / "out").iterdir()} == {
        "decoy.zip",
        "payload.txt",
    }
