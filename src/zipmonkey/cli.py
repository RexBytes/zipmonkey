"""Command-line interface: a thin argparse wrapper over the library.

This module exists only to translate argv into library calls and return-code
conventions; all behaviour lives in the library modules so the CLI and the
Python API cannot drift apart. Subcommands: ``inspect``, ``tree``, ``extract``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .archive import Archive, ArchiveReadError, UnsupportedArchiveError
from .models import ArchiveEntry
from .safety import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_MEMBER_BYTES,
    DEFAULT_MAX_TOTAL_BYTES,
    ArchiveLimitError,
)


def _human_size(n: int) -> str:
    """Format a byte count as a short human string (e.g. ``1.5K``, ``2.0M``).

    Carries to the next unit when rounding would otherwise display ``1024.0``
    of the current unit (so ``1048575`` reads ``1.0M``, not ``1024.0K``).
    """
    units = ["B", "K", "M", "G", "T", "P"]
    size = float(n)
    i = 0
    while i < len(units) - 1 and size >= 1024:
        size /= 1024
        i += 1
    if i < len(units) - 1 and round(size, 1) >= 1024:
        size /= 1024
        i += 1
    if units[i] == "B":
        return f"{int(size)}{units[i]}"
    return f"{size:.1f}{units[i]}"


def _entry_type(entry: ArchiveEntry) -> str:
    if entry.is_dir:
        return "dir"
    return entry.detected_type or "?"


def _cmd_inspect(args: argparse.Namespace) -> int:
    with Archive(args.archive, password=_pw(args)) as arc:
        report = arc.inspect()
    print(f"archive: {report.path}")
    print(f"format:  {report.format}")
    print(f"files:   {report.file_count}")
    print(f"size:    {_human_size(report.total_size)}")
    print(f"ratio:   {report.compression_ratio:.2f}")
    print(f"artifacts: {report.artifact_count}")
    print("---")
    for e in report.entries:
        if e.is_dir:
            continue
        flag = "A" if e.is_artifact else "-"
        print(
            f"{flag} {_human_size(e.size):>8}  "
            f"{_entry_type(e):<8}  {e.name}"
        )
    return 0


def _cmd_tree(args: argparse.Namespace) -> int:
    with Archive(args.archive, password=_pw(args)) as arc:
        report = arc.inspect()
    print(report.path.name)
    # Build a nested mapping of directories -> children for a stable tree.
    names = sorted(
        e.name.replace("\\", "/").rstrip("/") for e in report.entries if e.name
    )
    meta = {
        e.name.replace("\\", "/").rstrip("/"): e for e in report.entries
    }
    # A prefix that other members live under is a directory, even if a file
    # member shares its name (the file/dir name clash recorded as a collision).
    parents: set[str] = set()
    for name in names:
        parts = name.split("/")
        for depth in range(1, len(parts)):
            parents.add("/".join(parts[:depth]))
    seen: set[str] = set()
    for name in names:
        parts = name.split("/")
        for depth in range(len(parts)):
            prefix = "/".join(parts[: depth + 1])
            if prefix in seen:
                continue
            seen.add(prefix)
            indent = "  " * depth
            leaf = parts[depth]
            entry = meta.get(prefix)
            is_file = (
                entry is not None
                and not entry.is_dir
                and prefix not in parents
            )
            if is_file and entry is not None:
                print(f"{indent}{leaf}  ({_human_size(entry.size)})")
            else:
                print(f"{indent}{leaf}/")
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    with Archive(args.archive, password=_pw(args)) as arc:
        result = arc.extract(
            args.dest,
            include=args.include,
            exclude=args.exclude,
            flat=args.flat,
            recursive=args.recursive,
            clean_artifacts=not args.keep_artifacts,
            overwrite=not args.no_overwrite,
            max_depth=args.max_depth,
            max_total_bytes=args.max_total_bytes,
            max_files=args.max_files,
            max_member_bytes=args.max_member_bytes,
        )
    print(f"extracted {result.count} file(s) to {result.dest}")
    if result.skipped_artifacts:
        print(f"skipped {len(result.skipped_artifacts)} artifact(s)")
    if result.skipped_unsafe:
        print(f"skipped {len(result.skipped_unsafe)} unsafe path(s)")
    if result.nested_extracted:
        print(
            f"unpacked {len(result.nested_extracted)} nested archive(s) "
            f"(containers kept on disk)"
        )
    verbose_buckets = (
        ("filtered", result.skipped_filtered),
        ("collision", result.skipped_collisions),
        ("existing", result.skipped_existing),
        ("link/special", result.skipped_links),
        ("over-depth nested", result.skipped_nested),
    )
    if args.verbose:
        # Surface every skip bucket so nothing is silently dropped.
        for label, bucket in verbose_buckets:
            if bucket:
                print(f"skipped {len(bucket)} {label}: {', '.join(bucket)}")
    else:
        hidden = sum(len(bucket) for _, bucket in verbose_buckets)
        if hidden:
            print(
                f"{hidden} other member(s) skipped; "
                f"rerun with --verbose for details"
            )
    return 0


def _pw(args: argparse.Namespace) -> bytes | None:
    return args.password.encode("utf-8") if args.password else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zipmonkey",
        description="Smart archive inspection and extraction.",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="password for encrypted archives (accepted before or after "
        "the subcommand)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def _add_password(sp: argparse.ArgumentParser) -> None:
        # SUPPRESS default so omitting it after the subcommand does not clobber
        # a value given before the subcommand.
        sp.add_argument(
            "--password",
            default=argparse.SUPPRESS,
            help="password for encrypted archives",
        )

    p_inspect = sub.add_parser("inspect", help="summarise without extracting")
    p_inspect.add_argument("archive", type=Path)
    _add_password(p_inspect)
    p_inspect.set_defaults(func=_cmd_inspect)

    p_tree = sub.add_parser("tree", help="print archive contents as a tree")
    p_tree.add_argument("archive", type=Path)
    _add_password(p_tree)
    p_tree.set_defaults(func=_cmd_tree)

    p_extract = sub.add_parser("extract", help="extract archive contents")
    p_extract.add_argument("archive", type=Path)
    p_extract.add_argument("dest", type=Path)
    p_extract.add_argument("--include", action="append", help="glob to include")
    p_extract.add_argument("--exclude", action="append", help="glob to exclude")
    p_extract.add_argument("--flat", action="store_true", help="flatten output")
    p_extract.add_argument(
        "--recursive", action="store_true", help="unpack nested archives"
    )
    p_extract.add_argument(
        "--keep-artifacts",
        action="store_true",
        help="do not strip OS junk (__MACOSX, .DS_Store, ...)",
    )
    p_extract.add_argument(
        "--no-overwrite",
        action="store_true",
        help="skip members whose target already exists",
    )
    p_extract.add_argument(
        "--max-depth",
        type=int,
        default=DEFAULT_MAX_DEPTH,
        help="max nesting depth with --recursive (0 = no depth cap; "
        "recursion still bounded by --max-files/--max-total-bytes)",
    )
    p_extract.add_argument(
        "--max-total-bytes",
        type=int,
        default=DEFAULT_MAX_TOTAL_BYTES,
        help="cap on total bytes written (decompression-bomb guard; 0 disables)",
    )
    p_extract.add_argument(
        "--max-files",
        type=int,
        default=DEFAULT_MAX_FILES,
        help="cap on total files written (fan-out guard; 0 disables)",
    )
    p_extract.add_argument(
        "--max-member-bytes",
        type=int,
        default=DEFAULT_MAX_MEMBER_BYTES,
        help="per-member uncompressed size cap (esp. for 7z; 0 disables)",
    )
    p_extract.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="report every skip bucket (filtered, collisions, existing, ...)",
    )
    _add_password(p_extract)
    p_extract.set_defaults(func=_cmd_extract)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (UnsupportedArchiveError, ArchiveReadError, ArchiveLimitError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        # PermissionError, NotADirectoryError, disk-full, etc.
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
