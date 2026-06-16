"""Detection of OS-generated junk files that pollute archives.

This module exists to centralise the single question "is this archive member
operating-system noise that the user almost certainly did not mean to ship?"
so that extraction, inspection, and walking all agree on the same answer.
"""

from __future__ import annotations

# Exact basenames that are always OS junk regardless of location.
_ARTIFACT_BASENAMES = frozenset(
    {
        ".DS_Store",
        ".localized",
        "Thumbs.db",
        "ehthumbs.db",
        "ehthumbs_vista.db",
        "Desktop.ini",
        "desktop.ini",
    }
)

# Path components (directories) that mark an entire subtree as junk.
_ARTIFACT_DIRS = frozenset(
    {
        "__MACOSX",
        ".Spotlight-V100",
        ".Trashes",
        ".fseventsd",
        ".TemporaryItems",
        ".DocumentRevisions-V100",
    }
)


def is_os_artifact(name: str) -> bool:
    """Return True if an archive member name is OS-generated junk.

    A member is junk when **any** of the following hold:

    * any path component equals a known junk directory (``__MACOSX``,
      ``.Spotlight-V100``, ``.Trashes``, ``.fseventsd``, ``.TemporaryItems``,
      ``.DocumentRevisions-V100``);
    * its basename is a known junk file (``.DS_Store``, ``Thumbs.db``,
      ``Desktop.ini``, ``.localized``, and Windows thumbnail caches);
    * its basename is an AppleDouble resource fork — a name beginning with
      ``._`` (this is how macOS stores metadata for *every* file inside a
      non-HFS archive).

    Both ``/`` and ``\\`` are accepted as separators so the check works on
    member names produced on any platform. The empty string is not an
    artifact.

    Args:
        name: An archive member path (e.g. ``"__MACOSX/._photo.jpg"``).

    Returns:
        True if the member should be treated as disposable OS noise.
    """
    if not name:
        return False

    normalized = name.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p]
    if not parts:
        return False

    # Any junk directory anywhere in the path taints the whole member.
    if any(part in _ARTIFACT_DIRS for part in parts):
        return True

    basename = parts[-1]
    if basename in _ARTIFACT_BASENAMES:
        return True

    # AppleDouble resource forks: "._<original name>".
    if basename.startswith("._"):
        return True

    return False
