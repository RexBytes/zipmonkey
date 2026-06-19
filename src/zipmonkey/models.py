"""Result and value types returned by the public API.

This module exists so that every value crossing the public API boundary is a
typed ``@dataclass`` rather than an ad-hoc dict. Downstream code (and the LLM
agents that consume this package) gets autocomplete, type-checking, and a
stable field contract instead of guessing dict keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ArchiveEntry:
    """A single member of an archive, described without extracting it.

    Attributes:
        name: The member path *as stored in the archive*, using forward
            slashes. Directory members end in ``/``.
        size: Uncompressed size in bytes. ``0`` for directories.
        compressed_size: Stored (compressed) size in bytes. ``0`` for
            directories and for formats that do not report it.
        is_dir: True when the member is a directory entry.
        is_artifact: True when the member is an OS-generated junk file
            (see :func:`zipmonkey.artifacts.is_os_artifact`).
        is_special: True when the member is neither a regular file nor a
            directory — a symlink, hardlink, device, or FIFO (only tar stores
            these). Special members are skipped during extraction rather than
            materialised.
        detected_type: A short type label (e.g. ``"csv"``, ``"pdf"``,
            ``"zip"``) inferred from magic bytes, or ``None`` when not
            inspected. Directories always carry ``None``.
    """

    name: str
    size: int
    compressed_size: int
    is_dir: bool
    is_artifact: bool
    is_special: bool = False
    detected_type: str | None = None

    @property
    def compression_ratio(self) -> float:
        """Fraction of the original size retained after compression.

        Defined as ``compressed_size / size``. A value of ``0.25`` means the
        member was compressed to a quarter of its original size; ``1.0`` means
        no compression; values ``> 1.0`` mean the stored form is larger than
        the original (possible for tiny or already-compressed payloads).

        Returns ``1.0`` for zero-size members (a directory or empty file has
        nothing to compress, so "no space saved" is the honest answer).
        """
        if self.size <= 0:
            return 1.0
        return self.compressed_size / self.size


@dataclass(frozen=True)
class InspectReport:
    """Summary of an archive's contents produced without full extraction.

    Attributes:
        path: Filesystem path of the inspected archive.
        format: Detected container format (``"zip"``, ``"tar"``,
            ``"tar.gz"``, ``"tar.bz2"``, ``"tar.xz"``, ``"7z"``, ``"rar"``).
        entries: All members, in archive order.
        total_size: Sum of uncompressed sizes across non-directory members.
        total_compressed: Sum of compressed sizes across non-directory
            members.
        artifact_count: Number of members flagged as OS artifacts.
    """

    path: Path
    format: str
    entries: list[ArchiveEntry]
    total_size: int
    total_compressed: int
    artifact_count: int

    @property
    def file_count(self) -> int:
        """Number of non-directory members."""
        return sum(1 for e in self.entries if not e.is_dir)

    @property
    def compression_ratio(self) -> float:
        """Overall ``total_compressed / total_size`` for the archive.

        Returns ``1.0`` when ``total_size`` is zero (nothing to compress).
        """
        if self.total_size <= 0:
            return 1.0
        return self.total_compressed / self.total_size


@dataclass(frozen=True)
class TypedFile:
    """An extracted file tagged with a detected type for dispatch.

    Yielded by :meth:`zipmonkey.archive.Archive.walk_typed`. The ``category``
    field maps a file to the ecosystem package best suited to process it.

    Attributes:
        path: Absolute filesystem path of the extracted file.
        detected_type: Specific type label from magic bytes / extension
            (e.g. ``"csv"``, ``"pdf"``, ``"xlsx"``, ``"text"``, ``"unknown"``).
        category: Coarse dispatch bucket, one of ``"tabular"``, ``"pdf"``,
            ``"excel"``, ``"archive"``, ``"other"``.
    """

    path: Path
    detected_type: str
    category: str


@dataclass(frozen=True)
class ExtractResult:
    """Outcome of an extraction operation.

    Attributes:
        dest: Directory the members were written into.
        extracted: Absolute paths of files actually written, in extraction
            order.
        skipped_artifacts: Member names skipped because they were OS
            artifacts.
        skipped_filtered: Member names skipped because of include/exclude
            filters.
        skipped_unsafe: Member names skipped because their target path
            could not be safely placed under ``dest``: it escaped ``dest``
            (path-traversal protection), contained NUL/control characters, or
            was rejected by the filesystem (e.g. a path component exceeded the
            maximum name length).
        skipped_collisions: Member names skipped because their target path
            collided with an already-written file/directory of the same name
            (an archive containing both ``foo`` and ``foo/bar`` cannot place
            both on a normal filesystem).
        skipped_existing: Member names skipped because their target already
            existed and ``overwrite=False`` was requested.
        skipped_links: Member names skipped because they were special members
            (symlink/hardlink/device/FIFO); see ``ArchiveEntry.is_special``.
        skipped_nested: Absolute paths (as strings) of nested archives that
            were *not* recursively unpacked because they sat beyond
            ``max_depth``. The container file is left on disk untouched.
        nested_extracted: Absolute paths of nested archives that *were*
            recursively unpacked (only populated when ``recursive=True``).
            Nested archive containers are recorded here, not in ``extracted``;
            ``extracted`` holds only leaf (non-archive) files.
    """

    dest: Path
    extracted: list[Path] = field(default_factory=list)
    skipped_artifacts: list[str] = field(default_factory=list)
    skipped_filtered: list[str] = field(default_factory=list)
    skipped_unsafe: list[str] = field(default_factory=list)
    skipped_collisions: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    skipped_links: list[str] = field(default_factory=list)
    skipped_nested: list[str] = field(default_factory=list)
    nested_extracted: list[Path] = field(default_factory=list)

    @property
    def count(self) -> int:
        """Number of *leaf* files written (excludes nested archive containers)."""
        return len(self.extracted)

    @property
    def written_count(self) -> int:
        """Total files written to disk.

        Counts leaf files (:attr:`extracted`), recursively-unpacked nested
        archive containers (:attr:`nested_extracted`), and over-depth
        containers left un-unpacked but still written (:attr:`skipped_nested`).
        This is what the ``max_files`` cap measures, so it can exceed
        :attr:`count` whenever recursive extraction wrote container files too.
        """
        return (
            len(self.extracted)
            + len(self.nested_extracted)
            + len(self.skipped_nested)
        )
