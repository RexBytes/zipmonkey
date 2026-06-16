"""Contract tests for is_os_artifact (Boolean: all four truth-table corners)."""

from __future__ import annotations

import pytest

from zipmonkey.artifacts import is_os_artifact


@pytest.mark.parametrize(
    "name",
    [
        "__MACOSX/foo.txt",  # junk dir at root
        "a/b/__MACOSX/c.txt",  # junk dir nested
        ".DS_Store",
        "sub/.DS_Store",
        "._resource",  # AppleDouble at root
        "dir/._photo.jpg",  # AppleDouble nested
        "Thumbs.db",
        "Desktop.ini",
        "desktop.ini",
        ".Spotlight-V100/x",
        ".Trashes/y",
        "a\\__MACOSX\\b",  # backslash separators
    ],
)
def test_confirmed_artifacts(name):
    assert is_os_artifact(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "data.csv",
        "sub/report.pdf",
        "README.md",
        "folder/file.DS_Store.txt",  # contains but is not .DS_Store
        "macosx/file.txt",  # not the magic dir (case/exact)
        "a/b/c.png",
    ],
)
def test_confirmed_non_artifacts(name):
    assert is_os_artifact(name) is False


def test_empty_string_is_not_artifact():
    # False for a different reason: there is no name to classify.
    assert is_os_artifact("") is False


def test_slash_only_is_not_artifact():
    assert is_os_artifact("///") is False


def test_adversarial_dotunderscore_only():
    # "._" prefix is the rule; a bare "._" basename still matches.
    assert is_os_artifact("._") is True


def test_underscore_file_not_appledouble():
    # "_foo" is a single underscore, not the "._" resource-fork prefix.
    assert is_os_artifact("_foo.txt") is False


def test_macosx_as_filename_not_dir():
    # A *file* literally named __MACOSX is still a junk component.
    assert is_os_artifact("__MACOSX") is True
