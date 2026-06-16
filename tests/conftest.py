"""Shared fixtures: builders for real archives used across the suite."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest


def make_zip(path: Path, members: dict[str, bytes], *, compress: bool = True) -> Path:
    """Write a zip at ``path`` from a {name: bytes} mapping."""
    mode = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    with zipfile.ZipFile(path, "w", mode) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def make_tar(path: Path, members: dict[str, bytes], *, mode: str = "w:gz") -> Path:
    """Write a tar (compression chosen by ``mode``) from a mapping."""
    with tarfile.open(path, mode) as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


@pytest.fixture
def zip_factory(tmp_path):
    def _factory(name: str, members: dict[str, bytes], **kw) -> Path:
        return make_zip(tmp_path / name, members, **kw)

    return _factory


@pytest.fixture
def tar_factory(tmp_path):
    def _factory(name: str, members: dict[str, bytes], **kw) -> Path:
        return make_tar(tmp_path / name, members, **kw)

    return _factory


@pytest.fixture
def messy_zip(tmp_path):
    """A realistic messy archive: junk, nested zip, mixed types, subdirs."""
    inner = tmp_path / "inner.zip"
    make_zip(inner, {"data.csv": b"a,b\n1,2\n", "notes.txt": b"hello"})
    outer = tmp_path / "messy.zip"
    with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("__MACOSX/._data.csv", b"junk")
        zf.writestr(".DS_Store", b"junk")
        zf.writestr("report.pdf", b"%PDF-1.4 fake pdf body")
        zf.writestr("sub/a.csv", b"x,y\n3,4\n")
        zf.write(inner, "nested/inner.zip")
    return outer
