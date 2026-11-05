"""Test that the operator's real name is not written in the author attribution of the public repo.

Under the rule that the real name is not used without permission and is not written in any PR, it is not written in package metadata, author fields, or license documents;
the only attribution is Proboscis <3684241+proboscis@users.noreply.github.com>.

Since the real name is not written in this test either, check in two ways:
- Author fields (authors / maintainers in pyproject.toml, author / contributors in package.json) pass only entries
  on the allowed-attribution list (an allow list that needs no names written).
- Text outside author fields (AUTHORS, LICENSE, CITATION, README, the authors section of docs/index.md) goes red if even one word's lowercase sha256
  matches the hash of the real surname (only the hash is held, never the name itself).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Attributions allowed in author fields (any other name or email is red).
ALLOWED_NAME = "Proboscis"
ALLOWED_EMAIL = "3684241+proboscis@users.noreply.github.com"

# Lowercase sha256 of the real surname (the name itself is not held).
FORBIDDEN_TOKEN_HASHES = frozenset({"8de42984b32a1d5f161c938ee941f300f7d829049d9dc368c7721d4a4f17051a"})

# Files checked for names outside author fields (selected by the names of git-tracked files).
PROSE_NAME_PATTERN = re.compile(r"(^|/)(AUTHORS[^/]*|LICENSE[^/]*|CITATION[^/]*|README[^/]*)$")
PROSE_EXTRA = frozenset({"docs/index.md"})
WORD = re.compile(r"[A-Za-z]+")


def listed_files(root: Path) -> list[str]:
    """Return the list of paths of files to check under root (without checking things outside the working tree or ignored files).

    In a git working tree, the files git tracks. For a copy without .git (the daily full verification syncs without carrying .git via remote_check.py --tree .),
    create an empty git dir temporarily, point the working tree at the copy, and count the "non-ignored files" according to the copy's .gitignore.
    The copy holds only the original working tree's tracked files and non-ignored untracked files, so the set is the same
    (generated items such as the copy's .venv are excluded by .gitignore). The index of a parent repo outside root is not read.
    """
    if (root / ".git").exists():
        listed = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True)
    else:
        with tempfile.TemporaryDirectory(prefix="no-real-name-git-") as scratch:
            subprocess.run(["git", "init", "--quiet", scratch], capture_output=True, text=True, check=True)
            listed = subprocess.run(
                ["git", "--git-dir", str(Path(scratch) / ".git"), "--work-tree", str(root),
                 "ls-files", "--others", "--exclude-standard"],
                cwd=root, capture_output=True, text=True, check=True)
    return sorted(line for line in listed.stdout.splitlines() if line)


def tracked_files() -> list[str]:
    """Return the list of paths of files to check in this repo (listed_files at the repo root)."""
    return listed_files(ROOT)


def author_entries(path: str) -> list[object]:
    """Extract all author-field entries from one metadata file (pyproject.toml and package.json)."""
    text = (ROOT / path).read_text(encoding="utf-8")
    if path.endswith("pyproject.toml"):
        project = tomllib.loads(text).get("project", {})
        return [*project.get("authors", []), *project.get("maintainers", [])]
    document = json.loads(text)
    author = document.get("author")
    return [*([author] if author is not None else []), *document.get("contributors", [])]


def disallowed_author(entry: object) -> bool:
    """Whether one author-field entry is outside the allowed-attribution list (the package.json string form "name <email>" is also read)."""
    if isinstance(entry, dict):
        return entry.get("name") != ALLOWED_NAME or entry.get("email", ALLOWED_EMAIL) != ALLOWED_EMAIL
    if isinstance(entry, str):
        return entry not in {ALLOWED_NAME, f"{ALLOWED_NAME} <{ALLOWED_EMAIL}>"}
    return True


def test_author_fields_name_only_the_allowed_identity() -> None:
    # All author-field entries in pyproject.toml and package.json are only the allowed attribution Proboscis (and the noreply email).
    metadata = [p for p in tracked_files() if p.endswith(("pyproject.toml", "package.json"))
                and "/node_modules/" not in p and "/tests/data/" not in p]
    found = [f"{path}: {entry!r}" for path in metadata for entry in author_entries(path) if disallowed_author(entry)]
    assert found == [], "Entries in author fields outside the allowed attribution:\n" + "\n".join(found)


def test_prose_files_do_not_carry_the_real_name() -> None:
    # Text outside author fields (AUTHORS, LICENSE, CITATION, README, docs/index.md) contains no word of the real surname.
    prose = [p for p in tracked_files() if (PROSE_NAME_PATTERN.search(p) or p in PROSE_EXTRA) and "/tests/data/" not in p]
    found = []
    for path in prose:
        text = (ROOT / path).read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            if any(hashlib.sha256(word.lower().encode()).hexdigest() in FORBIDDEN_TOKEN_HASHES for word in WORD.findall(line)):
                found.append(f"{path}:{number}")
    assert found == [], "Lines containing a word of the real surname (the name is not shown):\n" + "\n".join(found)


def test_the_listing_reads_a_copy_without_git() -> None:
    # Failure case (#1201): the daily full verification runs on a copy that does not carry .git. Even on a copy, count the same set as git ls-files of the original working tree,
    # and do not count generated items excluded by .gitignore (the README inside the .venv built in the copy). The pre-fix form (git ls-files on the copy)
    # fails with "not a git repository" (status 128).
    with tempfile.TemporaryDirectory(prefix="no-real-name-") as scratch:
        source = Path(scratch) / "source"
        for path, text in {".gitignore": ".venv/\n", "README.md": "doc\n", "packages/a/LICENSE": "license\n",
                           "packages/a/pyproject.toml": "[project]\n"}.items():
            (source / path).parent.mkdir(parents=True, exist_ok=True)
            (source / path).write_text(text, encoding="utf-8")
        subprocess.run(["git", "init", "--quiet", str(source)], capture_output=True, check=True)
        subprocess.run(["git", "-C", str(source), "add", "--all"], capture_output=True, check=True)
        copy = Path(scratch) / "copy"
        shutil.copytree(source, copy, ignore=shutil.ignore_patterns(".git"))
        (copy / ".venv" / "lib").mkdir(parents=True)
        (copy / ".venv" / "lib" / "README.md").write_text("vendored\n", encoding="utf-8")
        assert not (copy / ".git").exists()
        expected = [".gitignore", "README.md", "packages/a/LICENSE", "packages/a/pyproject.toml"]
        assert listed_files(source) == expected
        assert listed_files(copy) == expected


def test_the_name_check_catches_a_planted_name() -> None:
    # The check works: a word matching the name hash is red, and the allowed attribution passes (the name itself is not written, and the word behind the hash is not constructed).
    assert disallowed_author({"name": "Somebody Else", "email": ALLOWED_EMAIL})
    assert disallowed_author({"name": ALLOWED_NAME, "email": "someone@example.com"})
    assert not disallowed_author({"name": ALLOWED_NAME, "email": ALLOWED_EMAIL})
    assert not disallowed_author(ALLOWED_NAME)
    assert hashlib.sha256(b"proboscis").hexdigest() not in FORBIDDEN_TOKEN_HASHES
