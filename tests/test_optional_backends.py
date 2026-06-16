"""Tests for the optional 7z / rar backends.

These are gated behind ``importorskip`` so they run only where the optional
dependency is installed, and exercise the same contract as the stdlib
backends: format label, entry shape, and a read round-trip. A separate test
pins the "missing dependency" error path, which can run anywhere the lib is
absent.
"""

from __future__ import annotations

import pytest

import zipmonkey
from zipmonkey.archive import _SevenZipBackend, _RarBackend, UnsupportedArchiveError


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