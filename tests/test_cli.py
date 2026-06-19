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
        (1047552, "1023.0K"),  # 1023 KiB, stays in K
        (1048575, "1.0M"),  # rolls over instead of "1024.0K"
        (1024 * 1024, "1.0M"),
        (1024 * 1024 * 1024, "1.0G"),
        (1024**4, "1.0T"),
        (1024**5, "1.0P"),
        (1024**6, "1.0E"),
        (1024**7, "1.0Z"),
        (1024**8, "1.0Y"),
        # regression: the carry guard used to give up at the last unit, so a
        # value rounding to 1024.0 of the prior unit rendered "1024.0P".
        (1_152_865_209_611_504_832, "1.0E"),
        (2**63, "8.0E"),
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


def test_cli_wrong_password_returns_1(tmp_path, capsys):
    import shutil
    import subprocess

    if shutil.which("zip") is None:  # pragma: no cover - env dependent
        pytest.skip("`zip` CLI not available to build an encrypted fixture")
    (tmp_path / "s.txt").write_bytes(b"classified")
    enc = tmp_path / "secret.zip"
    subprocess.run(
        ["zip", "-j", "-P", "hunter2", str(enc), str(tmp_path / "s.txt")],
        check=True,
        capture_output=True,
    )
    # Wrong password must produce a friendly error + exit 1, not a traceback.
    rc = main(["inspect", str(enc), "--password", "WRONG"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "error:" in err


def test_cli_corrupt_zip_returns_1(tmp_path, capsys):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"PK\x03\x04 corrupt not a real zip")
    rc = main(["inspect", str(bad)])
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_cli_requires_subcommand(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_password_accepted_after_subcommand():
    args = build_parser().parse_args(["inspect", "a.zip", "--password", "x"])
    assert args.password == "x"


def test_password_accepted_before_subcommand():
    args = build_parser().parse_args(["--password", "x", "inspect", "a.zip"])
    assert args.password == "x"


def test_password_default_none_either_position():
    args = build_parser().parse_args(["inspect", "a.zip"])
    assert args.password is None


def test_tree_file_dir_name_clash_renders_dir(tmp_path, capsys):
    # Archive with both "foo" (file) and "foo/bar.txt": the shared prefix must
    # render as a directory, not a file with children.
    z = tmp_path / "clash.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("foo", b"12345678")
        zf.writestr("foo/bar.txt", b"hello")
    rc = main(["tree", str(z)])
    out = capsys.readouterr().out
    assert rc == 0
    assert out == ("clash.zip\n" "foo/\n" "  bar.txt  (5B)\n")


def test_inspect_real_compression_ratio(tmp_path):
    # DEFLATED, moderately compressible -> a real ratio strictly inside (0, 1).
    import zipmonkey

    z = tmp_path / "c.zip"
    payload = (b"abc123XYZ " * 400)  # 4000 bytes, partly compressible
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("z.txt", payload)
    rep = zipmonkey.inspect(z)
    # total_compressed must be wired from real per-member sizes, not constant.
    assert 0 < rep.total_compressed < rep.total_size
    assert 0.0 < rep.compression_ratio < 1.0


def test_extract_cli_verbose_reports_skip_buckets(tmp_path, capsys):
    z = tmp_path / "v.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("keep.csv", b"1")
        zf.writestr("drop.log", b"2")
    rc = main(
        ["extract", str(z), str(tmp_path / "o"), "--exclude", "*.log", "-v"]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "skipped 1 filtered: drop.log" in out


def test_extract_cli_no_verbose_omits_skip_buckets(tmp_path, capsys):
    z = tmp_path / "v.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("keep.csv", b"1")
        zf.writestr("drop.log", b"2")
    rc = main(["extract", str(z), str(tmp_path / "o"), "--exclude", "*.log"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "filtered" not in out  # detail withheld without --verbose
    # ...but a hint that something was skipped is shown.
    assert "1 other member(s) skipped; rerun with --verbose" in out


def test_extract_cli_max_files_limit(tmp_path, capsys):
    z = tmp_path / "m.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for i in range(4):
            zf.writestr(f"f{i}.txt", b"x")
    rc = main(["extract", str(z), str(tmp_path / "o"), "--max-files", "2"])
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_extract_cli_dest_under_file_returns_2(tmp_path, capsys):
    z = _fixed_zip(tmp_path / "g.zip")
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"x")
    rc = main(["extract", str(z), str(blocker / "sub")])
    assert rc == 2
    assert "error:" in capsys.readouterr().err
