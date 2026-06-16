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

    def peek(self, name, n):  # pragma: no cover - not reached
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


def test_sevenzip_extract(tmp_path):
    archive = _make_7z(tmp_path, {"a.csv": b"x,y\n1,2\n", "b.txt": b"hello"})
    res = zipmonkey.extract(archive, tmp_path / "out")
    assert res.count == 2
    assert (tmp_path / "out" / "a.csv").read_bytes() == b"x,y\n1,2\n"


def test_sevenzip_include_filter(tmp_path):
    archive = _make_7z(tmp_path, {"a.csv": b"1", "b.log": b"2"})
    res = zipmonkey.extract(archive, tmp_path / "out", include="*.csv")
    assert {p.name for p in res.extracted} == {"a.csv"}


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
