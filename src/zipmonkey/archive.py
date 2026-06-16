"""Unified archive handle over ZIP and tar families, with optional 7z/rar.

This module exists to collapse the repetitive ``zipfile`` / ``tarfile``
boilerplate into one object that inspects, extracts, filters, flattens, and
recursively unpacks archives while cleaning OS junk and guarding against
malicious paths, decompression bombs, and fan-out bombs. It is the package's
primary entry point via :func:`zipmonkey.open`.
"""

from __future__ import annotations

import builtins
import bz2
import fnmatch
import gzip
import lzma
import shutil
import stat
import tarfile
import tempfile
import zipfile
import zlib
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .artifacts import is_os_artifact
from .detect import category_for, detect_type, is_archive_type
from .models import ArchiveEntry, ExtractResult, InspectReport, TypedFile
from .safety import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_TOTAL_BYTES,
    check_file_count,
    check_total_bytes,
    safe_target,
)

_PEEK_BYTES = 520  # 262 suffices for signatures (tar magic ends at 262); the
# extra bytes sharpen the textual UTF-8 heuristic in detect_type.
_CHUNK = 1 << 20  # 1 MiB streaming chunk

# Exception types that mean "this stream is not the (compressed) archive we
# thought it was" rather than a programming error.
_DECOMP_ERRORS = (
    tarfile.ReadError,
    EOFError,
    OSError,
    lzma.LZMAError,
    zlib.error,
)


class UnsupportedArchiveError(ValueError):
    """Raised when a path is not a recognised / supported archive format."""


class ArchiveReadError(ValueError):
    """Raised when a member cannot be read (wrong/missing password, corrupt data).

    Normalises the assorted low-level errors the backends raise at read time
    (e.g. ``zipfile``'s ``RuntimeError: Bad password``, ``zlib``/``lzma`` decode
    errors) into one package-level exception, so callers and the CLI get a
    consistent, catchable failure instead of a library-specific traceback.
    """


# Low-level errors that mean "this member could not be read" (as opposed to a
# safety-limit breach, which is ArchiveLimitError, a RuntimeError subclass that
# must never be swallowed here).
_READ_ERRORS = (RuntimeError, zlib.error, lzma.LZMAError, EOFError)


def _as_read_error(name: str, exc: BaseException) -> ArchiveReadError:
    return ArchiveReadError(f"cannot read member {name!r}: {exc}")


# --------------------------------------------------------------------------- #
# Backends: each wraps one underlying library behind a tiny common surface.
# --------------------------------------------------------------------------- #


class _Backend:
    format: str

    #: Whether ``open_stream`` returns a genuinely incremental stream that does
    #: not materialise the whole member in memory. When False, the extraction
    #: layer preflights each member's declared size against ``max_total_bytes``
    #: before reading it, since the chunked write cannot bound peak memory.
    streaming: bool = True

    def entries(self) -> list[ArchiveEntry]:  # pragma: no cover - interface
        raise NotImplementedError

    def read(self, name: str) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError

    def peek(self, name: str, n: int) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError

    def open_stream(self, name: str) -> BinaryIO | None:  # pragma: no cover
        """Return a readable binary stream for ``name`` (or ``None`` for dirs).

        Contract: implementations should return a stream that reads
        incrementally, so callers can bound memory by reading in chunks. A
        backend that cannot stream (its library only exposes whole-member
        decompression) must set ``streaming = False`` so the extraction layer
        applies a declared-size preflight instead of relying on the chunked
        write to bound memory.
        """
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class _ZipBackend(_Backend):
    format = "zip"

    def __init__(self, path: Path, password: bytes | None) -> None:
        self._zf = zipfile.ZipFile(path, "r")
        if password is not None:
            self._zf.setpassword(password)

    def entries(self) -> list[ArchiveEntry]:
        out: list[ArchiveEntry] = []
        for info in self._zf.infolist():
            # ZIP stores the Unix mode in the high 16 bits of external_attr;
            # a symlink there must be treated as special (its body is the link
            # target text), not materialised as a regular file.
            mode = info.external_attr >> 16
            is_link = stat.S_ISLNK(mode) if mode else False
            out.append(
                ArchiveEntry(
                    name=info.filename,
                    size=info.file_size,
                    compressed_size=info.compress_size,
                    is_dir=info.is_dir(),
                    is_artifact=is_os_artifact(info.filename),
                    is_special=is_link,
                )
            )
        return out

    def read(self, name: str) -> bytes:
        return self._zf.read(name)

    def peek(self, name: str, n: int) -> bytes:
        with self._zf.open(name) as fh:
            return fh.read(n)

    def open_stream(self, name: str) -> BinaryIO:
        return self._zf.open(name)  # type: ignore[return-value]

    def close(self) -> None:
        self._zf.close()


class _TarBackend(_Backend):
    _SUFFIX = {"tar": "tar", "gzip": "tar.gz", "bzip2": "tar.bz2", "xz": "tar.xz"}

    def __init__(self, path: Path, outer: str) -> None:
        # "r:*" lets tarfile transparently handle the compression layer.
        self._tf = tarfile.open(path, "r:*")
        self.format = self._SUFFIX.get(outer, "tar")
        self._members = {m.name: m for m in self._tf.getmembers()}

    def entries(self) -> list[ArchiveEntry]:
        out: list[ArchiveEntry] = []
        for m in self._tf.getmembers():
            out.append(
                ArchiveEntry(
                    name=m.name,
                    size=m.size if m.isfile() else 0,
                    # tar compresses the whole stream, not per member.
                    compressed_size=0,
                    is_dir=m.isdir(),
                    is_artifact=is_os_artifact(m.name),
                    is_special=not m.isfile() and not m.isdir(),
                )
            )
        return out

    def read(self, name: str) -> bytes:
        member = self._members[name]
        # Only regular files have readable content. extractfile() would resolve
        # a hard/soft link to its TARGET's bytes, contradicting the documented
        # "special members read as empty" contract, so guard on isfile().
        if not member.isfile():
            return b""
        fh = self._tf.extractfile(member)
        if fh is None:
            return b""
        try:
            return fh.read()
        finally:
            fh.close()

    def peek(self, name: str, n: int) -> bytes:
        member = self._members[name]
        if not member.isfile():
            return b""
        try:
            fh = self._tf.extractfile(member)
        except KeyError:
            return b""
        if fh is None:
            return b""
        try:
            return fh.read(n)
        finally:
            fh.close()

    def open_stream(self, name: str) -> BinaryIO | None:
        member = self._members[name]
        if not member.isfile():
            return None
        return self._tf.extractfile(member)  # type: ignore[return-value]

    def close(self) -> None:
        self._tf.close()


class _SingleFileBackend(_Backend):
    """A lone gzip/bzip2/xz-compressed file exposed as a one-member archive."""

    _SUFFIXES = {
        "gzip": (".gz",),
        "bzip2": (".bz2", ".bz"),
        "xz": (".xz", ".lzma"),
    }

    def __init__(self, path: Path, outer: str) -> None:
        self.format = outer
        self._path = path
        openers: dict[str, Callable[..., Any]] = {
            "gzip": gzip.open,
            "bzip2": bz2.open,
            "xz": lzma.open,
        }
        self._opener: Callable[..., Any] = openers[outer]
        self._compressed_size = path.stat().st_size

        member = path.name
        for suf in self._SUFFIXES[outer]:
            if member.lower().endswith(suf):
                member = member[: -len(suf)]
                break
        self._member = member or "data"
        self._size: int | None = None

    def validate(self) -> None:
        """Read one byte to confirm the compressed stream is well-formed."""
        with self._opener(self._path, "rb") as fh:
            fh.read(1)

    def _streamed_size(self) -> int:
        # Stream-count without holding the whole payload in memory (bomb-safe).
        if self._size is None:
            total = 0
            with self._opener(self._path, "rb") as fh:
                while True:
                    chunk = fh.read(_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
            self._size = total
        return self._size

    def entries(self) -> list[ArchiveEntry]:
        return [
            ArchiveEntry(
                name=self._member,
                size=self._streamed_size(),
                compressed_size=self._compressed_size,
                is_dir=False,
                is_artifact=is_os_artifact(self._member),
            )
        ]

    def read(self, name: str) -> bytes:
        with self._opener(self._path, "rb") as fh:
            return fh.read()

    def peek(self, name: str, n: int) -> bytes:
        with self._opener(self._path, "rb") as fh:
            return fh.read(n)

    def open_stream(self, name: str) -> BinaryIO:
        return self._opener(self._path, "rb")  # type: ignore[return-value]

    def close(self) -> None:
        pass


class _SevenZipBackend(_Backend):
    format = "7z"
    # py7zr exposes only whole-member decompression (read() -> BytesIO), so this
    # backend cannot stream; the extraction layer preflights declared sizes and
    # the limitation is documented in LIMITATIONS.md.
    streaming = False

    def __init__(self, path: Path, password: bytes | None) -> None:
        try:
            import py7zr
        except ImportError as exc:  # pragma: no cover - env dependent
            raise UnsupportedArchiveError(
                "7z support requires the optional dependency py7zr "
                "(pip install zipmonkey[sevenzip])"
            ) from exc
        pwd = password.decode("utf-8") if password is not None else None
        self._py7zr = py7zr
        self._path = path
        self._password = pwd
        with py7zr.SevenZipFile(path, mode="r", password=pwd) as zf:
            self._info = list(zf.list())

    def entries(self) -> list[ArchiveEntry]:
        out: list[ArchiveEntry] = []
        for item in self._info:
            out.append(
                ArchiveEntry(
                    name=item.filename,
                    size=getattr(item, "uncompressed", 0) or 0,
                    compressed_size=getattr(item, "compressed", 0) or 0,
                    is_dir=bool(getattr(item, "is_directory", False)),
                    is_artifact=is_os_artifact(item.filename),
                )
            )
        return out

    def read(self, name: str) -> bytes:
        with self._py7zr.SevenZipFile(
            self._path, mode="r", password=self._password
        ) as zf:
            data = zf.read([name])
            return data[name].read()

    def peek(self, name: str, n: int) -> bytes:
        return self.read(name)[:n]

    def open_stream(self, name: str) -> BinaryIO:
        import io

        return io.BytesIO(self.read(name))

    def close(self) -> None:
        pass


class _RarBackend(_Backend):
    format = "rar"

    def __init__(self, path: Path, password: bytes | None) -> None:
        try:
            import rarfile
        except ImportError as exc:  # pragma: no cover - env dependent
            raise UnsupportedArchiveError(
                "rar support requires the optional dependency rarfile "
                "(pip install zipmonkey[rar])"
            ) from exc
        self._rf = rarfile.RarFile(path)
        if password is not None:
            self._rf.setpassword(password.decode("utf-8"))

    def entries(self) -> list[ArchiveEntry]:
        out: list[ArchiveEntry] = []
        for info in self._rf.infolist():
            out.append(
                ArchiveEntry(
                    name=info.filename,
                    size=info.file_size,
                    compressed_size=getattr(info, "compress_size", 0) or 0,
                    is_dir=info.isdir(),
                    is_artifact=is_os_artifact(info.filename),
                )
            )
        return out

    def read(self, name: str) -> bytes:
        return self._rf.read(name)

    def peek(self, name: str, n: int) -> bytes:
        with self._rf.open(name) as fh:
            return fh.read(n)

    def open_stream(self, name: str) -> BinaryIO:
        return self._rf.open(name)  # type: ignore[return-value]

    def close(self) -> None:
        self._rf.close()


def _sniff_compression(path: Path, head: bytes) -> str | None:
    """Identify a compression wrapper with no recognisable magic (lzma-alone).

    Magic-bearing wrappers are already handled by ``detect_type``; this only
    rescues the legacy lzma "alone" format, which carries no signature. lzma's
    auto-detecting reader handles both xz and alone, so it is reported under
    the ``"xz"`` family.
    """
    t = detect_type(head)
    if t in ("gzip", "bzip2", "xz"):
        return t
    try:
        with lzma.open(path, "rb") as fh:
            fh.read(1)
        return "xz"
    except _DECOMP_ERRORS:
        return None


def _open_backend(path: Path, password: bytes | None) -> _Backend:
    """Pick and construct the right backend by sniffing the file's magic.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        UnsupportedArchiveError: If the format is unrecognised or the stream is
            corrupt (never lets a raw decompression error escape).
    """
    if not path.exists():
        raise FileNotFoundError(f"no such archive: {path}")
    if path.is_dir():
        raise UnsupportedArchiveError(f"path is a directory, not an archive: {path}")

    with builtins.open(path, "rb") as fh:
        head = fh.read(_PEEK_BYTES)

    # Sniff the *container* type only (ignore filename so xlsx -> zip, etc.).
    outer = detect_type(head)

    if outer == "zip":
        try:
            return _ZipBackend(path, password)
        except zipfile.BadZipFile as exc:
            raise UnsupportedArchiveError(
                f"corrupt or unsupported zip archive: {path}"
            ) from exc
    if outer == "7z":
        return _build_optional("7z", lambda: _SevenZipBackend(path, password), path)
    if outer == "rar":
        return _build_optional("rar", lambda: _RarBackend(path, password), path)

    if outer in ("gzip", "bzip2", "xz"):
        try:
            return _TarBackend(path, outer)
        except _DECOMP_ERRORS:
            return _single_file_or_unsupported(path, outer)

    if outer == "tar":
        try:
            return _TarBackend(path, "tar")
        except _DECOMP_ERRORS as exc:
            raise UnsupportedArchiveError(
                f"corrupt or unsupported tar archive: {path}"
            ) from exc

    # Magic-less: try a compression sniff (lzma-alone), then last-resort tar.
    comp = _sniff_compression(path, head)
    if comp is not None:
        try:
            return _TarBackend(path, comp)
        except _DECOMP_ERRORS:
            return _single_file_or_unsupported(path, comp)
    try:
        return _TarBackend(path, "tar")
    except _DECOMP_ERRORS as exc:
        raise UnsupportedArchiveError(
            f"unrecognised or unsupported archive format: {path}"
        ) from exc


def _build_optional(kind: str, factory, path: Path) -> _Backend:
    """Construct an optional-dependency backend, normalising corrupt-file errors.

    A missing optional dependency already surfaces as UnsupportedArchiveError;
    any other construction failure (a corrupt 7z/rar with archive magic) is
    re-raised as UnsupportedArchiveError so the contract holds for every format.
    """
    try:
        return factory()
    except UnsupportedArchiveError:
        raise
    except Exception as exc:  # noqa: BLE001 - backend boundary normalisation
        raise UnsupportedArchiveError(
            f"corrupt or unsupported {kind} archive: {path}"
        ) from exc


def _single_file_or_unsupported(path: Path, outer: str) -> _Backend:
    """Construct a validated single-file backend or raise UnsupportedArchive."""
    backend = _SingleFileBackend(path, outer)
    try:
        backend.validate()
    except _DECOMP_ERRORS as exc:
        raise UnsupportedArchiveError(
            f"corrupt or unsupported {outer} stream: {path}"
        ) from exc
    return backend


# --------------------------------------------------------------------------- #
# Filtering / naming helpers
# --------------------------------------------------------------------------- #


def _as_patterns(value: str | Sequence[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    """Case-insensitive glob match against full path *or* basename.

    Patterns are globs (``fnmatch`` semantics): ``*``, ``?``, and ``[seq]`` are
    wildcards, so a literal member name containing those characters must escape
    them. Folding uses ``str.casefold`` for correct Unicode case handling.
    """
    if not patterns:
        return False
    norm = name.replace("\\", "/").casefold()
    base = norm.rsplit("/", 1)[-1]
    for pat in patterns:
        p = pat.casefold()
        if fnmatch.fnmatch(norm, p) or fnmatch.fnmatch(base, p):
            return True
    return False


def _passes_filter(
    name: str, include: tuple[str, ...], exclude: tuple[str, ...]
) -> bool:
    """Exclude wins over include; no include patterns means "accept all"."""
    if exclude and _matches(name, exclude):
        return False
    if include and not _matches(name, include):
        return False
    return True


def _split_ext(basename: str) -> tuple[str, str]:
    dot = basename.rfind(".")
    if dot > 0:
        return basename[:dot], basename[dot:]
    return basename, ""


def _unique_basename(basename: str, dest: Path, used: set[str]) -> str:
    """Return a collision-free basename within ``dest``.

    Suffixes ``" (n)"`` before the extension, checking both the names already
    chosen this run (``used``) and files already present on disk in ``dest`` so
    a pre-populated destination never loses or overwrites data.
    """
    candidate = basename
    if candidate not in used and not (dest / candidate).exists():
        used.add(candidate)
        return candidate
    stem, ext = _split_ext(basename)
    i = 1
    while True:
        candidate = f"{stem} ({i}){ext}"
        if candidate not in used and not (dest / candidate).exists():
            used.add(candidate)
            return candidate
        i += 1


def _unique_dir(path: Path) -> Path:
    """Return a non-existent directory path, suffixing ``" (n)"`` if needed."""
    if not path.exists():
        return path
    i = 1
    while True:
        candidate = path.with_name(f"{path.name} ({i})")
        if not candidate.exists():
            return candidate
        i += 1


@dataclass
class _Ctx:
    """Mutable extraction context shared across the whole recursive operation."""

    max_total_bytes: int
    max_files: int
    max_depth: int
    clean_artifacts: bool
    overwrite: bool
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    flat: bool
    total_bytes: int = 0
    file_count: int = 0


# --------------------------------------------------------------------------- #
# Archive: the public handle
# --------------------------------------------------------------------------- #


class Archive:
    """An open archive supporting inspection, extraction, and walking.

    Prefer the context-manager form, which also removes any temporary
    directories created by ``dest``-less extraction::

        with zipmonkey.open("bundle.zip") as arc:
            for tf in arc.walk_typed():
                ...  # temp files cleaned up automatically on exit

    Constructing an ``Archive`` directly is supported but then you are
    responsible for calling :meth:`close` to release the file handle and clean
    temp directories. As a backstop, ``__del__`` also calls :meth:`close`.
    """

    def __init__(self, path: str | Path, *, password: bytes | None = None) -> None:
        self.path = Path(path)
        self._password = password
        self._backend = _open_backend(self.path, password)
        self._temp_dirs: list[Path] = []
        self._closed = False

    # -- lifecycle ------------------------------------------------------- #

    def __enter__(self) -> Archive:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - GC timing dependent
        try:
            self.close()
        except Exception:
            pass

    def close(self) -> None:
        """Close the backend and delete temp dirs created during extraction."""
        if self._closed:
            return
        self._closed = True
        try:
            self._backend.close()
        finally:
            for d in self._temp_dirs:
                shutil.rmtree(d, ignore_errors=True)
            self._temp_dirs.clear()

    # -- introspection --------------------------------------------------- #

    @property
    def format(self) -> str:
        """Detected container format (e.g. ``"zip"``, ``"tar.gz"``)."""
        return self._backend.format

    def entries(self) -> list[ArchiveEntry]:
        """Return all members (without ``detected_type`` populated)."""
        return self._backend.entries()

    def namelist(self) -> list[str]:
        """Return all member names, in archive order."""
        return [e.name for e in self._backend.entries()]

    def read(self, name: str) -> bytes:
        """Read one member's uncompressed bytes by name, into memory.

        A convenience for trusted or small members: the entire uncompressed
        payload is materialised, so it does **not** honour the extraction byte
        caps. For large or untrusted members use :meth:`open_member` and read
        in bounded chunks, or :meth:`extract` (which streams under a cap).

        Returns ``b""`` for a directory or special (link/device) member.

        Raises:
            ArchiveReadError: If the member cannot be read (e.g. wrong/missing
                password or corrupt data).
        """
        try:
            return self._backend.read(name)
        except _READ_ERRORS as exc:
            raise _as_read_error(name, exc) from exc

    def open_member(self, name: str) -> BinaryIO:
        """Return a readable binary stream for one member (caller closes it).

        The streaming counterpart to :meth:`read`: read in bounded chunks
        instead of materialising the whole payload, e.g.::

            with arc.open_member("big.bin") as fh:
                while chunk := fh.read(1 << 20):
                    ...

        This raw stream is not subject to the extraction byte caps; enforce
        your own limit while reading untrusted input.

        Raises:
            ArchiveReadError: If the member cannot be opened (e.g. wrong/missing
                password).
        """
        try:
            stream = self._backend.open_stream(name)
        except _READ_ERRORS as exc:
            raise _as_read_error(name, exc) from exc
        if stream is None:  # directory / special member
            import io

            return io.BytesIO(b"")
        return stream

    def inspect(self, *, detect_types: bool = True) -> InspectReport:
        """Summarise the archive without extracting it.

        Args:
            detect_types: When True (default) each regular member's leading
                bytes are read to populate ``detected_type``. Set False to skip
                that I/O for a faster, type-less summary.

        Returns:
            An :class:`~zipmonkey.models.InspectReport`.
        """
        raw = self._backend.entries()
        entries: list[ArchiveEntry] = []
        total_size = 0
        total_compressed = 0
        artifact_count = 0

        for e in raw:
            dtype: str | None = None
            if detect_types and not e.is_dir and not e.is_special:
                try:
                    head = self._backend.peek(e.name, _PEEK_BYTES)
                except _READ_ERRORS as exc:
                    raise _as_read_error(e.name, exc) from exc
                dtype = detect_type(head, filename=e.name)
            entries.append(
                ArchiveEntry(
                    name=e.name,
                    size=e.size,
                    compressed_size=e.compressed_size,
                    is_dir=e.is_dir,
                    is_artifact=e.is_artifact,
                    is_special=e.is_special,
                    detected_type=dtype,
                )
            )
            if not e.is_dir:
                total_size += e.size
                total_compressed += e.compressed_size
            if e.is_artifact:
                artifact_count += 1

        return InspectReport(
            path=self.path,
            format=self._backend.format,
            entries=entries,
            total_size=total_size,
            total_compressed=total_compressed,
            artifact_count=artifact_count,
        )

    # -- extraction ------------------------------------------------------ #

    def extract(
        self,
        dest: str | Path | None = None,
        *,
        include: str | Sequence[str] | None = None,
        exclude: str | Sequence[str] | None = None,
        flat: bool = False,
        recursive: bool = False,
        clean_artifacts: bool = True,
        overwrite: bool = True,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
        max_files: int = DEFAULT_MAX_FILES,
    ) -> ExtractResult:
        """Extract members to ``dest`` with filtering, flattening, recursion.

        Args:
            dest: Destination directory. When ``None`` a temporary directory is
                created and tracked for automatic removal on :meth:`close` /
                context exit. Created if it does not exist.
            include: Glob pattern(s); when given, only matching *leaf* files are
                extracted. Matched case-insensitively against both the full
                member path and its basename. Nested archive containers are
                always traversed under ``recursive`` regardless of the filter,
                so a leaf-only filter still reaches inside them.
            exclude: Glob pattern(s) to drop. Exclude always overrides include.
            flat: When True, every leaf file is written directly into ``dest``
                using its basename; collisions (including with files already in
                ``dest``) get a ``" (n)"`` suffix. Flattening also applies to
                nested-archive contents under ``recursive``.
            recursive: When True, any member that is itself an archive is
                unpacked — into a sibling ``<name>_extracted`` directory (or,
                under ``flat``, straight into ``dest``) — up to ``max_depth``
                levels of nesting.
            clean_artifacts: When True (default) OS-junk members are skipped.
            overwrite: When False, a member whose target already exists is
                skipped and recorded in ``skipped_existing``.
            max_depth: Maximum nested-archive nesting depth. ``max_depth=1``
                unpacks one level of nesting; archives beyond the limit are left
                on disk and listed in ``skipped_nested``. ``<= 0`` disables the
                depth cap (unlimited nesting, still bounded by
                ``max_total_bytes`` and ``max_files``) — it does not turn off
                recursion, which is controlled by ``recursive``.
            max_total_bytes: Cap on cumulative bytes written to disk across the
                whole operation. Enforced *while streaming*, so a single huge
                member cannot exhaust memory. Exceeding it raises
                :class:`~zipmonkey.safety.ArchiveLimitError` (with the partial
                result attached). ``<= 0`` disables.
            max_files: Cap on the total number of files written to disk —
                including nested-archive containers, so it matches
                ``ExtractResult.written_count`` rather than ``count`` (which is
                leaf-only). Bounds fan-out bombs. Exceeding it raises
                ``ArchiveLimitError``. ``<= 0`` disables.

        Returns:
            An :class:`~zipmonkey.models.ExtractResult` recording what was
            written and what was skipped and why. Nested archive containers go
            in ``nested_extracted``; ``extracted`` holds only leaf files.
        """
        if dest is None:
            tmp = Path(tempfile.mkdtemp(prefix="zipmonkey_"))
            self._temp_dirs.append(tmp)
            dest = tmp
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        # Resolve to an absolute, symlink-free path so every path in the
        # ExtractResult (dest, extracted, nested_extracted) is absolute, as the
        # model documents — even when the caller passed a relative dest.
        dest = dest.resolve()

        ctx = _Ctx(
            max_total_bytes=max_total_bytes,
            max_files=max_files,
            max_depth=max_depth,
            clean_artifacts=clean_artifacts,
            overwrite=overwrite,
            include=_as_patterns(include),
            exclude=_as_patterns(exclude),
            flat=flat,
        )
        result = ExtractResult(dest=dest)
        self._extract_into(
            backend=self._backend,
            dest=dest,
            ctx=ctx,
            result=result,
            recursive=recursive,
            depth=0,
            flat_used=set(),
        )
        return result

    def _extract_into(
        self,
        *,
        backend: _Backend,
        dest: Path,
        ctx: _Ctx,
        result: ExtractResult,
        recursive: bool,
        depth: int,
        flat_used: set[str],
    ) -> None:
        for e in backend.entries():
            if e.is_dir:
                continue
            if e.is_special:
                result.skipped_links.append(e.name)
                continue
            if ctx.clean_artifacts and e.is_artifact:
                result.skipped_artifacts.append(e.name)
                continue

            is_arc = False
            if recursive:
                head = backend.peek(e.name, _PEEK_BYTES)
                # Pass the filename so zip-container documents (xlsx/docx/jar/…)
                # are classified as leaves, not unpacked as raw zips.
                is_arc = is_archive_type(detect_type(head, filename=e.name))

            # The include/exclude filter applies to leaf files only; archive
            # containers are always traversed so a leaf filter reaches inside.
            if not is_arc and not _passes_filter(e.name, ctx.include, ctx.exclude):
                result.skipped_filtered.append(e.name)
                continue

            target = self._target_for(e.name, dest, ctx, flat_used, result)
            if target is None:
                continue  # already recorded as unsafe

            if not ctx.overwrite and target.exists():
                result.skipped_existing.append(e.name)
                continue

            # Preflight caps BEFORE writing so the over-limit member is never
            # materialised on disk. file_count is only incremented after a
            # successful (non-collision) write, so the prospective +1 is exact.
            check_file_count(
                ctx.file_count + 1, ctx.max_files, partial_result=result
            )
            # Non-streaming backends (7z) materialise the whole member before
            # the chunked write can count it, so preflight the *declared*
            # uncompressed size against the budget to reject oversized members
            # before any decompression happens.
            if not backend.streaming and ctx.max_total_bytes > 0:
                check_total_bytes(
                    ctx.total_bytes + e.size,
                    ctx.max_total_bytes,
                    partial_result=result,
                )

            status = self._write_member(backend, e.name, target, ctx, result)
            if status == "collision":
                result.skipped_collisions.append(e.name)
                continue

            ctx.file_count += 1

            if is_arc:
                if ctx.max_depth <= 0 or depth + 1 <= ctx.max_depth:
                    unpacked = self._recurse_one(
                        archive_path=target,
                        top_dest=dest,
                        ctx=ctx,
                        result=result,
                        depth=depth + 1,
                        flat_used=flat_used,
                    )
                    if unpacked:
                        result.nested_extracted.append(target)
                    else:
                        # Archive magic but not a valid archive: it is a leaf.
                        result.extracted.append(target)
                else:
                    result.skipped_nested.append(str(target))
            else:
                result.extracted.append(target)

    def _target_for(
        self,
        name: str,
        dest: Path,
        ctx: _Ctx,
        flat_used: set[str],
        result: ExtractResult,
    ) -> Path | None:
        # safe_target validates traversal/NUL/control for both modes.
        resolved = safe_target(dest, name)
        if resolved is None:
            result.skipped_unsafe.append(name)
            return None
        if ctx.flat:
            return dest / _unique_basename(resolved.name, dest, flat_used)
        return resolved

    def _write_member(
        self,
        backend: _Backend,
        name: str,
        target: Path,
        ctx: _Ctx,
        result: ExtractResult,
    ) -> str:
        # A file/dir name clash (archive has both "foo" and "foo/bar") cannot
        # be represented on a normal filesystem: report it as a collision.
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except (FileExistsError, NotADirectoryError):
            return "collision"
        if target.exists() and target.is_dir():
            return "collision"

        try:
            stream = backend.open_stream(name)
        except _READ_ERRORS as exc:
            raise _as_read_error(name, exc) from exc
        if stream is None:
            try:
                target.write_bytes(b"")
            except (IsADirectoryError, NotADirectoryError):
                return "collision"
            return "written"
        try:
            with builtins.open(target, "wb") as out:
                while True:
                    try:
                        chunk = stream.read(_CHUNK)
                    except _READ_ERRORS as exc:
                        # Wrong password / corrupt member mid-stream: drop the
                        # partial file and surface a normalised error.
                        out.close()
                        target.unlink(missing_ok=True)
                        raise _as_read_error(name, exc) from exc
                    if not chunk:
                        break
                    ctx.total_bytes += len(chunk)
                    if (
                        ctx.max_total_bytes > 0
                        and ctx.total_bytes > ctx.max_total_bytes
                    ):
                        out.close()
                        target.unlink(missing_ok=True)
                        check_total_bytes(
                            ctx.total_bytes,
                            ctx.max_total_bytes,
                            partial_result=result,
                        )
                    out.write(chunk)
        except (IsADirectoryError, NotADirectoryError):
            return "collision"
        finally:
            stream.close()
        return "written"

    def _recurse_one(
        self,
        *,
        archive_path: Path,
        top_dest: Path,
        ctx: _Ctx,
        result: ExtractResult,
        depth: int,
        flat_used: set[str],
    ) -> bool:
        """Recursively extract ``archive_path``; return False if it won't open.

        A False return means the file had archive magic but is not a valid
        archive, so the caller should treat it as a normal leaf file.
        """
        # Nested archives reuse the top-level password (a common case is one
        # password protecting the whole bundle). A nested archive needing a
        # different password will surface its library's error at read time.
        try:
            sub_backend = _open_backend(archive_path, self._password)
        except (UnsupportedArchiveError, FileNotFoundError):
            return False

        if ctx.flat:
            sub_dest = top_dest
            sub_flat_used = flat_used
        else:
            sub_dest = _unique_dir(
                archive_path.with_name(archive_path.name + "_extracted")
            )
            sub_flat_used = set()

        try:
            sub_dest.mkdir(parents=True, exist_ok=True)
            self._extract_into(
                backend=sub_backend,
                dest=sub_dest,
                ctx=ctx,
                result=result,
                recursive=True,
                depth=depth,
                flat_used=sub_flat_used,
            )
        finally:
            sub_backend.close()
        return True

    # -- typed walking --------------------------------------------------- #

    def walk_typed(
        self,
        dest: str | Path | None = None,
        *,
        recursive: bool = True,
        **extract_kwargs: object,
    ) -> Iterator[TypedFile]:
        """Extract, then yield each leaf file tagged by detected type.

        Files are extracted (recursively by default so nested archives are
        unpacked) and then each *leaf* file is classified from its magic bytes
        and name. Nested archive containers are not yielded — only the leaf
        files they contain. Yields :class:`~zipmonkey.models.TypedFile` so
        callers can dispatch to the right processor (tabular -> dsvmonkey,
        pdf -> pdfmonkey, excel -> xldetect/xlfilldown).

        Args:
            dest: Where to extract (temp dir if ``None``, cleaned on close).
            recursive: Passed through to :meth:`extract`.
            **extract_kwargs: Forwarded to :meth:`extract`.

        Yields:
            One :class:`TypedFile` per extracted leaf file, in extraction order.
        """
        result = self.extract(dest, recursive=recursive, **extract_kwargs)  # type: ignore[arg-type]
        for path in result.extracted:
            try:
                with builtins.open(path, "rb") as fh:
                    head = fh.read(_PEEK_BYTES)
            except OSError:
                continue
            dtype = detect_type(head, filename=path.name)
            yield TypedFile(
                path=path,
                detected_type=dtype,
                category=category_for(dtype),
            )


def open(path: str | Path, *, password: bytes | None = None) -> Archive:  # noqa: A001
    """Open an archive and return an :class:`Archive` handle.

    The format is detected from the file's magic bytes, not its extension, so a
    mislabelled archive still opens correctly. Use as a context manager for
    automatic cleanup of temporary extraction directories::

        with zipmonkey.open("data.zip") as arc:
            report = arc.inspect()

    Args:
        path: Path to the archive file.
        password: Optional password as bytes (ZIP/7z/rar). A wrong or missing
            password surfaces as an error from the underlying library at read
            time, not at open time.

    Returns:
        An open :class:`Archive`.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        UnsupportedArchiveError: If the format is not recognised, or the stream
            is corrupt.
    """
    return Archive(path, password=password)
