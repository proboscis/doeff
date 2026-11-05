"""Test that the version of docs/releases/v<version>.md (the draft of the body to paste into a GitHub Release) matches a section of CHANGELOG.md.

The body names its version by file name (docs/releases/v0.8.0.md is the body of Release v0.8.0). That version has a section
`## <version> — <date>` in CHANGELOG.md, and every version the body names (links to tags, links to CHANGELOG sections) must be the same
as the version in the file name -- so that a spot left unfixed after copying the previous version's body does not go out in a Release still pointing at another version's tag or section.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RELEASES = REPO / "docs" / "releases"
CHANGELOG = REPO / "CHANGELOG.md"

_NOTES_NAME = re.compile(r"v(?P<version>\d+\.\d+\.\d+)\.md")
_SECTION = re.compile(r"^## (?P<version>\d+\.\d+\.\d+) — (?P<date>\d{4}-\d{2}-\d{2})$", re.MULTILINE)
# Links to tags in the body (blob/v0.8.0/..., tree/v0.8.0/..., releases/tag/v0.8.0) and anchors to CHANGELOG sections (#080--2026-10-27).
_TAG_LINK = re.compile(r"/(?:blob|tree|releases/tag)/v(?P<version>\d+\.\d+\.\d+)\b")
_CHANGELOG_ANCHOR = re.compile(r"CHANGELOG\.md#(?P<anchor>[0-9a-z-]+)")


def changelog_sections(changelog: str) -> dict[str, str]:
    """Version -> date of the CHANGELOG's version sections."""
    return {m["version"]: m["date"] for m in _SECTION.finditer(changelog)}


def section_anchor(version: str, date: str) -> str:
    """The anchor GitHub gives to the heading `## 0.8.0 — 2026-10-27` (`080--2026-10-27`)."""
    return f"{version.replace('.', '')}--{date}"


def mismatches(file_name: str, notes: str, changelog: str) -> list[str]:
    """List of places where one body disagrees with the CHANGELOG (empty if they match)."""
    named = _NOTES_NAME.fullmatch(file_name)
    if named is None:
        return [f"{file_name}: the name is not v<major>.<minor>.<patch>.md"]
    version = named["version"]
    sections = changelog_sections(changelog)
    if version not in sections:
        return [f"{file_name}: CHANGELOG.md has no '## {version} — <date>' section"]
    anchor = section_anchor(version, sections[version])
    wrong_tags = [
        f"{file_name}: links to the tag v{m['version']}, not v{version}"
        for m in _TAG_LINK.finditer(notes)
        if m["version"] != version
    ]
    wrong_anchors = [
        f"{file_name}: links to CHANGELOG.md#{m['anchor']}, not CHANGELOG.md#{anchor}"
        for m in _CHANGELOG_ANCHOR.finditer(notes)
        if m["anchor"] != anchor
    ]
    unnamed = [] if version in notes else [f"{file_name}: the text never names {version}"]
    return [*wrong_tags, *wrong_anchors, *unnamed]


def _notes_files() -> list[Path]:
    return sorted(RELEASES.glob("*.md"))


def test_there_are_release_notes() -> None:
    assert [path.name for path in _notes_files()], "docs/releases/ has no release notes"


@pytest.mark.parametrize("path", _notes_files(), ids=lambda path: path.name)
def test_release_notes_match_a_changelog_section(path: Path) -> None:
    assert mismatches(path.name, path.read_text(encoding="utf-8"), CHANGELOG.read_text(encoding="utf-8")) == []


_CHANGELOG_EXAMPLE = "# Changelog\n\n## Unreleased\n\n## 0.8.0 — 2026-10-27\n\n- a change\n\n## 0.7.0 — 2026-10-18\n"


@pytest.mark.parametrize(
    ("file_name", "notes", "expected"),
    [
        ("v0.8.0.md", "doeff 0.8.0 … [CHANGELOG](CHANGELOG.md#080--2026-10-27)", []),
        ("v0.9.0.md", "doeff 0.9.0", ["v0.9.0.md: CHANGELOG.md has no '## 0.9.0 — <date>' section"]),
        ("0.8.0.md", "doeff 0.8.0", ["0.8.0.md: the name is not v<major>.<minor>.<patch>.md"]),
        (
            "v0.8.0.md",
            "doeff 0.8.0, see https://github.com/o/r/blob/v0.7.0/README.md",
            ["v0.8.0.md: links to the tag v0.7.0, not v0.8.0"],
        ),
        (
            "v0.8.0.md",
            "doeff 0.8.0, see CHANGELOG.md#070--2026-10-18",
            ["v0.8.0.md: links to CHANGELOG.md#070--2026-10-18, not CHANGELOG.md#080--2026-10-27"],
        ),
        ("v0.8.0.md", "doeff, a new version", ["v0.8.0.md: the text never names 0.8.0"]),
    ],
)
def test_mismatches_names_each_way_the_notes_and_the_changelog_disagree(
    file_name: str, notes: str, expected: list[str]
) -> None:
    assert mismatches(file_name, notes, _CHANGELOG_EXAMPLE) == expected
