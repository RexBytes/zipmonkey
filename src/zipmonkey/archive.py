"""Unified archive handle over ZIP and tar families, with optional 7z/rar.

This module exists to collapse the repetitive ``zipfile`` / ``tarfile``
boilerplate into one object that inspects, extracts, filters, flattens, and
recursively unpacks archives while cleaning OS junk and guarding against
malicious paths. It is the package's primary entry point via
:func:`zipmonkey.open`.
"""

from __future__ import annotations

import builtins
import fnmatch
import shutil
import tarfile
import tempfile
import warnings
import zipfile
from collections.abc import Iterator, Sequence
from pathlib import Path

from .artifacts import is_os_artifact
from .detect import category_for, detect_type, is_archive_type
from .models import ArchiveEntry, ExtractResult, InspectReport, TypedFile
from .safety import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_TOTAL_BYTES,
    check_depth,
    check_total_bytes,
    safe_target,
)

_PEEK_BYTES = 520  # enough for every magic signature (tar lives at 257)


class UnsupportedArchiveError(ValueError):
    """Raised when a path is not a recognised / supported archive format."""


# --------------------------------------------------------------------------- #
# Backends: each wraps one underlying library behind a tiny common surface.
# --------------------------------------------------------------------------- #


class _Backend:
    format: str

    def entries(self) -> list[ArchiveEntry]:  # pragma: no cover - interface
        raise NotImplementedError

    def read(self, name: str) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError

    def peek(self, name: str, n: int) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class _ZipBackend(_Backend):
    format = "zip"

    def __init__(self, path: Path, password: bytes | None) -> None:
        self._zf = zipfile.ZipFile(path, "r")
        if password is not None:
            self._zf.setpassword(password)
        self._infos = {info.filename: info for info in self._zf.infolist()}

    def entries(self) -> list[ArchiveEntry]:
        out: list[ArchiveEntry] = []
        for info in self._zf.infolist():
            is_dir = info.is_dir()
            out.append(
                ArchiveEntry(
                    name=info.filename,
                    size=info.file_size,
                    compressed_size=info.compress_size,
                    is_dir=is_dir,
                    is_artifact=is_os_artifact(info.filename),
                )
            )
        return out

    def read(self, name: str) -> bytes:
        return self._zf.read(name)

    def peek(self, name: str, n: int) -> bytes:
        with self._zf.open(name) as fh:
            return fh.read(n)

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
                )
            )
        return out

    def read(self, name: str) -> bytes:
        member = self._members[name]
        fh = self._tf.extractfile(member)
        if fh is None:
            return b""
        try:
            return fh.read()
        finally:
            fh.close()

    def peek(self, name: str, n: int) -> bytes:
        member = self._members[name]
        fh = self._tf.extractfile(member)
        if fh is None:
            return b""
        try:
            return fh.read(n)
        finally:
            fh.close()

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
        import bz2
        import gzip
        import lzma

        self.format = outer
        self._path = path
        opener = {"gzip": gzip.open, "bzip2": bz2.open, "xz": lzma.open}[outer]
        self._opener = opener
        self._compressed_size = path.stat().st_size

        member = path.name
        for suf in self._SUFFIXES[outer]:
            if member.lower().endswith(suf):
                member = member[: -len(suf)]
                break
        self._member = member or "data"
        self._cache: bytes | None = None

    def _data(self) -> bytes:
        if self._cache is None:
            with self._opener(self._path, "rb") as fh:
                self._cache = fh.read()
        return self._cache

    def entries(self) -> list[ArchiveEntry]:
        return [
            ArchiveEntry(
                name=self._member,
                size=len(self._data()),
                compressed_size=self._compressed_size,
                is_dir=False,
                is_artifact=is_os_artifact(self._member),
            )
        ]

    def read(self, name: str) -> bytes:
        return self._data()

    def peek(self, name: str, n: int) -> bytes:
        with self._opener(self._path, "rb") as fh:
            return fh.read(n)

    def close(self) -> None:
        self._cache = None


class _SevenZipBackend(_Backend):
    format = "7z"

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
            buf = data[name]
            return buf.read()

    def peek(self, name: str, n: int) -> bytes:
        return self.read(name)[:n]

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
        self._infos = {info.filename: info for info in self._rf.infolist()}

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

    def close(self) -> None:
        self._rf.close()


def _open_backend(path: Path, password: bytes | None) -> _Backend:
    """Pick and construct the right backend by sniffing the file's magic."""
    if not path.exists():
        raise FileNotFoundError(f"no such archive: {path}")
    if path.is_dir():
        raise UnsupportedArchiveError(f"path is a directory, not an archive: {path}")

    with builtins.open(path, "rb") as fh:
        head = fh.read(_PEEK_BYTES)

    # Sniff the *container* type only (ignore filename so xlsx -> zip, etc.).
    outer = detect_type(head)

    if outer == "zip":
        return _ZipBackend(path, password)
    if outer == "7z":
        return _SevenZipBackend(path, password)
    if outer == "rar":
        return _RarBackend(path, password)
    if outer in ("gzip", "bzip2", "xz"):
        try:
            return _TarBackend(path, outer)
        except tarfile.ReadError:
            return _SingleFileBackend(path, outer)
    if outer == "tar":
        return _TarBackend(path, "tar")

    # Last resort: GNU/old tar without a ustar magic number.
    try:
        return _TarBackend(path, "tar")
    except tarfile.ReadError as exc:
        raise UnsupportedArchiveError(
            f"unrecognised or unsupported archive format: {path}"
        ) from exc


# --------------------------------------------------------------------------- #
# Filtering helpers
# --------------------------------------------------------------------------- #


def _as_patterns(value: str | Sequence[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    """Case-insensitive glob match against full path *or* basename."""
    if not patterns:
        return False
    norm = name.replace("\\", "/").lower()
    base = norm.rsplit("/", 1)[-1]
    for pat in patterns:
        p = pat.lower()
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


def _unique_basename(basename: str, used: set[str]) -> str:
    """Return a collision-free basename, suffixing ``" (n)"`` before the ext."""
    if basename not in used:
        used.add(basename)
        return basename
    dot = basename.rfind(".")
    if dot > 0:
        stem, ext = basename[:dot], basename[dot:]
    else:
        stem, ext = basename, ""
    i = 1
    while True:
        candidate = f"{stem} ({i}){ext}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        i += 1


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
    temp directories.
    """

    def __init__(self, path: str | Path, *, password: bytes | None = None) -> None:
        self.path = Path(path)
        self._backend = _open_backend(self.path, password)
        self._temp_dirs: list[Path] = []
        self._closed = False

    # -- lifecycle ------------------------------------------------------- #

    def __enter__(self) -> Archive:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

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
        """Read one member's uncompressed bytes by name."""
        return self._backend.read(name)

    def inspect(self, *, detect_types: bool = True) -> InspectReport:
        """Summarise the archive without extracting it.

        Args:
            detect_types: When True (default) each non-directory member's
                leading bytes are read to populate ``detected_type``. Set
                False to skip that I/O for a faster, type-less summary.

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
            if detect_types and not e.is_dir:
                head = self._backend.peek(e.name, _PEEK_BYTES)
                dtype = detect_type(head, filename=e.name)
            entry = ArchiveEntry(
                name=e.name,
                size=e.size,
                compressed_size=e.compressed_size,
                is_dir=e.is_dir,
                is_artifact=e.is_artifact,
                detected_type=dtype,
            )
            entries.append(entry)
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
    ) -> ExtractResult:
        """Extract members to ``dest`` with filtering, flattening, recursion.

        Args:
            dest: Destination directory. When ``None`` a temporary directory is
                created and tracked for automatic removal on :meth:`close` /
                context exit. Created if it does not exist.
            include: Glob pattern(s); when given, only matching members are
                extracted. Matched case-insensitively against both the full
                member path and its basename.
            exclude: Glob pattern(s) to drop. Exclude always overrides include.
            flat: When True, every file is written directly into ``dest`` using
                its basename; name collisions get a ``" (n)"`` suffix.
            recursive: When True, any extracted member that is itself an
                archive is unpacked into a sibling ``<name>_extracted``
                directory, recursively up to ``max_depth``.
            clean_artifacts: When True (default) OS-junk members
                (``__MACOSX/``, ``.DS_Store``, AppleDouble ``._*`` …) are
                skipped.
            overwrite: When False, a member whose target already exists is
                skipped instead of overwritten.
            max_depth: Maximum nested-archive recursion depth (``<= 0`` to
                disable the limit).
            max_total_bytes: Cap on cumulative uncompressed output across the
                whole (possibly recursive) operation (``<= 0`` to disable).

        Returns:
            An :class:`~zipmonkey.models.ExtractResult` recording what was
            written and what was skipped and why. Members whose target path
            clashes with an already-written file/directory of the same name
            (an archive holding both ``foo`` and ``foo/bar``) are recorded in
            ``skipped_collisions`` rather than raising.
        """
        if dest is None:
            tmp = Path(tempfile.mkdtemp(prefix="zipmonkey_"))
            self._temp_dirs.append(tmp)
            dest = tmp
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)

        inc = _as_patterns(include)
        exc = _as_patterns(exclude)
        result = ExtractResult(dest=dest)
        counter = [0]  # running uncompressed byte total (shared with recursion)
        used: set[str] = set()

        self._extract_into(
            backend=self._backend,
            dest=dest,
            include=inc,
            exclude=exc,
            flat=flat,
            clean_artifacts=clean_artifacts,
            overwrite=overwrite,
            result=result,
            counter=counter,
            max_total_bytes=max_total_bytes,
            flat_used=used,
        )

        if recursive:
            self._recurse(
                files=list(result.extracted),
                result=result,
                depth=1,
                max_depth=max_depth,
                clean_artifacts=clean_artifacts,
                overwrite=overwrite,
                counter=counter,
                max_total_bytes=max_total_bytes,
            )

        return result

    def _extract_into(
        self,
        *,
        backend: _Backend,
        dest: Path,
        include: tuple[str, ...],
        exclude: tuple[str, ...],
        flat: bool,
        clean_artifacts: bool,
        overwrite: bool,
        result: ExtractResult,
        counter: list[int],
        max_total_bytes: int,
        flat_used: set[str],
    ) -> None:
        for e in backend.entries():
            if e.is_dir:
                continue
            if clean_artifacts and e.is_artifact:
                result.skipped_artifacts.append(e.name)
                continue
            if not _passes_filter(e.name, include, exclude):
                result.skipped_filtered.append(e.name)
                continue

            if flat:
                base = e.name.replace("\\", "/").rsplit("/", 1)[-1]
                if not base:
                    result.skipped_unsafe.append(e.name)
                    continue
                target = dest / _unique_basename(base, flat_used)
            else:
                resolved = safe_target(dest, e.name)
                if resolved is None:
                    result.skipped_unsafe.append(e.name)
                    continue
                target = resolved

            if not overwrite and target.exists():
                result.skipped_filtered.append(e.name)
                continue

            # A file/dir name clash (archive has both "foo" and "foo/bar")
            # cannot be represented on a normal filesystem: skip and record.
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
            except (FileExistsError, NotADirectoryError):
                result.skipped_collisions.append(e.name)
                continue
            if target.exists() and target.is_dir():
                result.skipped_collisions.append(e.name)
                continue

            data = backend.read(e.name)
            counter[0] += len(data)
            check_total_bytes(counter[0], max_total_bytes)

            try:
                target.write_bytes(data)
            except (IsADirectoryError, NotADirectoryError):
                result.skipped_collisions.append(e.name)
                continue
            result.extracted.append(target)

    def _recurse(
        self,
        *,
        files: list[Path],
        result: ExtractResult,
        depth: int,
        max_depth: int,
        clean_artifacts: bool,
        overwrite: bool,
        counter: list[int],
        max_total_bytes: int,
    ) -> None:
        check_depth(depth, max_depth)
        for f in files:
            try:
                with builtins.open(f, "rb") as fh:
                    head = fh.read(_PEEK_BYTES)
            except OSError:
                continue
            if not is_archive_type(detect_type(head)):
                continue

            sub_dest = f.with_name(f.name + "_extracted")
            try:
                sub_backend = _open_backend(f, None)
            except (UnsupportedArchiveError, FileNotFoundError):
                continue

            sub_result = ExtractResult(dest=sub_dest)
            try:
                sub_dest.mkdir(parents=True, exist_ok=True)
                self._extract_into(
                    backend=sub_backend,
                    dest=sub_dest,
                    include=(),
                    exclude=(),
                    flat=False,
                    clean_artifacts=clean_artifacts,
                    overwrite=overwrite,
                    result=sub_result,
                    counter=counter,
                    max_total_bytes=max_total_bytes,
                    flat_used=set(),
                )
            finally:
                sub_backend.close()

            result.nested_extracted.append(f)
            result.extracted.extend(sub_result.extracted)
            result.skipped_artifacts.extend(sub_result.skipped_artifacts)
            result.skipped_unsafe.extend(sub_result.skipped_unsafe)
            result.skipped_collisions.extend(sub_result.skipped_collisions)

            self._recurse(
                files=list(sub_result.extracted),
                result=result,
                depth=depth + 1,
                max_depth=max_depth,
                clean_artifacts=clean_artifacts,
                overwrite=overwrite,
                counter=counter,
                max_total_bytes=max_total_bytes,
            )

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
        unpacked) and then each written file is classified from its magic
        bytes and name. Yields :class:`~zipmonkey.models.TypedFile` so callers
        can dispatch to the right processor (tabular -> dsvmonkey,
        pdf -> pdfmonkey, excel -> xldetect/xlfilldown).

        Args:
            dest: Where to extract (temp dir if ``None``, cleaned on close).
            recursive: Passed through to :meth:`extract`.
            **extract_kwargs: Forwarded to :meth:`extract` (``include``,
                ``exclude``, ``flat``, etc.).

        Yields:
            One :class:`TypedFile` per extracted file, in extraction order.
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
        UnsupportedArchiveError: If the format is not recognised/supported.
    """
    return Archive(path, password=password)
