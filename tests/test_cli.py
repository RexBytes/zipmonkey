"""Golden-output and return-code tests for the CLI."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest

from zipmonkey.cli import _human_size, build_parser, main


@pytest.mark.parametrize(
    "n,expected",
    [
        (0, "0B"),
        (512, "512B"),
        (1023, "1023B"),
        (1024, "1.0K"),
        (1536, "1.5K"),
        (1024 * 1024, "1.0M"),
        (1024 * 1024 * 1024, "1.0G"),
    ],
)
def test_human_size(n, expected):
    assert _human_size(n) == expected


def _fixed_zip(path: Path) -> Path:
    # Stored (no compression) for deterministic sizes in golden output.
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("a.csv", b"x,y\n1,2\n")  # 8 bytes
        zf.writestr("docs/readme.txt", b"hello world!")  # 12 bytes
    return path


def test_tree_golden(tmp_path, capsys):
    z = _fixed_zip(tmp_path / "g.zip")
    rc = main(["tree", str(z)])
    out = capsys.readouterr().out
    assert rc == 0
    assert out == (
        "g.zip\n"
        "a.csv  (8B)\n"
        "docs/\n"
        "  readme.txt  (12B)\n"
    )


def test_inspect_golden(tmp_path, capsys):
    z = _fixed_zip(tmp_path / "g.zip")
    rc = main(["inspect", str(z)])
    out = capsys.readouterr().out
    assert rc == 0
    # Mask the volatile absolute archive path on the first line.
    masked = re.sub(r"archive: .*", "archive: <PATH>", out)
    assert masked == (
        "archive: <PATH>\n"
        "format:  zip\n"
        "files:   2\n"
        "size:    20B\n"
        "ratio:   1.00\n"
        "artifacts: 0\n"
        "---\n"
        "-       8B  csv       a.csv\n"
        "-      12B  text      docs/readme.txt\n"
    )


def test_extract_cli(tmp_path, capsys):
    z = _fixed_zip(tmp_path / "g.zip")
    dest = tmp_path / "out"
    rc = main(["extract", str(z), str(dest)])
    out = capsys.readouterr().out
    assert rc == 0
    assert f"extracted 2 file(s) to {dest}" in out
    assert (dest / "a.csv").exists()


def test_extract_cli_flat_recursive(tmp_path, capsys):
    inner = tmp_path / "inner.zip"
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("deep.txt", b"deep")
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.write(inner, "nested/inner.zip")
        zf.writestr("top.txt", b"top")
    rc = main(["extract", str(outer), str(tmp_path / "o"), "--recursive"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "nested archive" in out


def test_cli_missing_file_returns_2(tmp_path, capsys):
    rc = main(["inspect", str(tmp_path / "nope.zip")])
    err = capsys.readouterr().err
    assert rc == 2
    assert "error:" in err


def test_cli_bad_archive_returns_1(tmp_path, capsys):
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"\x00\x01\x02not an archive")
    rc = main(["inspect", str(junk)])
    assert rc == 1


def test_cli_requires_subcommand(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args([])
