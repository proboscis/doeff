"""Entry files contain no internal names.

Entry files = the files a first-time visitor reads first on GitHub and PyPI — the root README.md, docs/index.md, docs/README.md,
CONTRIBUTING.md, the GitHub issue and pull request templates (under .github/ISSUE_TEMPLATE/ and .github/PULL_REQUEST_TEMPLATE.md —
shown on the question and report screens), packages/*/README.md (also the PyPI description field), and the files under examples/.
Internal names = 4 kinds — names of the application systems built on top, work board and conversation ids, names of cluster nodes, and personal home paths.

The single source of the word list is INTERNAL_NAMES in this module, except the names of cluster nodes: those are names of one
site, so they are not written in this public tree. The site passes them in the environment variable DOEFF_INTERNAL_MACHINE_NAMES
(comma-separated; the check of node names is off when it is unset). It does not read the word list of the semgrep rule doeff-packages-have-no-application-vocabulary:
that one applies to all contents of generic packages as "application-system words", and includes conversation, budget, artifact and herdr.
In entry files these 4 words appear with their general meaning (a terminal backend named herdr, the time budget of doeff-hy-pytest),
so the entry-file list is narrowed to names and kept separately.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from doeff_core_effects import ReadEnvironment, subprocess_handler

from doeff import run, with_handlers

REPO_ROOT = Path(__file__).resolve().parents[1]

# The word list (this one place). Regexes per kind, case-insensitive. Spellings preceded by an alphanumeric (macp, seos) do not match.
INTERNAL_NAMES: dict[str, str] = {
    # Names of the application systems built on top, and their repo dirs. agora-redesign and agora-controllers also match on agora. What follows is not checked
    # (AcpProtocolError and kanban_view also match).
    "application system name": r"agora|kanban|acp|hypha|mediagen|sporocarp|controllers/|clients/hy",
    # Keys of work board cards and milestones (ki-, ms- plus 12 hex digits), and ids of requests, conversations and messages (lt-, c-, m- plus a 26-character ULID).
    "work board and conversation id": r"(?:ki|ms)-[0-9a-f]{12}(?![0-9a-z])|(?:lt|c|m)-[0-9a-hjkmnp-tv-z]{26}(?![0-9a-z])",
    # Paths under a person's home (Linux and macOS). Case-sensitive — /users/42 in a URL does not match. Example paths are written with ~ or
    # $HOME.
    "personal home path": r"(?-i:/home/|/Users/)[A-Za-z0-9_.-]+",
}

# Names of cluster nodes (names from kubectl get nodes) and of the site's own judges, read from the environment (module docstring).
MACHINE_NAMES_ENV = "DOEFF_INTERNAL_MACHINE_NAMES"


def machine_names_from(environ: Mapping[str, str]) -> tuple[str, ...]:
    """The node names the site passed in MACHINE_NAMES_ENV (comma-separated, blanks dropped)."""
    return tuple(name.strip() for name in environ.get(MACHINE_NAMES_ENV, "").split(",") if name.strip())


def name_patterns(machine_names: Sequence[str]) -> dict[str, re.Pattern[str]]:
    """The patterns per kind; with machine names, also the kind "cluster node name" (a spelling followed by a letter does not match)."""
    kinds = dict(INTERNAL_NAMES)
    if machine_names:
        kinds["cluster node name"] = "(?:" + "|".join(map(re.escape, machine_names)) + ")(?![a-z])"
    return {kind: re.compile(rf"(?i)(?<![a-z0-9])(?:{pattern})") for kind, pattern in kinds.items()}


_SITE_ENVIRONMENT = run(with_handlers([subprocess_handler], ReadEnvironment((MACHINE_NAMES_ENV,))))
_PATTERNS = name_patterns(machine_names_from({entry.name: entry.value for entry in _SITE_ENVIRONMENT}))

# Fixed entry files (paths from the root). Those under .github/ISSUE_TEMPLATE/, packages/*/README.md and examples/ are read from the tree.
FIXED_ENTRY_FILES = (
    "README.md",
    "docs/index.md",
    "docs/README.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "SECURITY.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
)


def internal_names_in(text: str, patterns: Mapping[str, re.Pattern[str]] | None = None) -> list[str]:
    """Return the internal names in text as `<kind>: <spelling>`, in order of appearance."""
    found = [
        (match.start(), f"{kind}: {match.group(0)}")
        for kind, pattern in (_PATTERNS if patterns is None else patterns).items()
        for match in pattern.finditer(text)
    ]
    return [name for _, name in sorted(found)]


def entry_files(root: Path) -> list[str]:
    """Entry files under root, as paths from root. Under examples/, all files except cache dirs (__pycache__ and dirs starting with .)."""
    fixed = [name for name in FIXED_ENTRY_FILES if (root / name).is_file()]
    issue_templates = sorted(
        str(path.relative_to(root)) for path in root.glob(".github/ISSUE_TEMPLATE/*") if path.is_file()
    )
    packages = sorted(str(path.relative_to(root)) for path in root.glob("packages/*/README.md"))
    examples = sorted(
        str(path.relative_to(root))
        for path in (root / "examples").rglob("*")
        if path.is_file()
        and not any(part == "__pycache__" or part.startswith(".") for part in path.relative_to(root).parts[:-1])
    )
    return fixed + issue_templates + packages + examples


def internal_names_in_entry_files(root: Path, patterns: Mapping[str, re.Pattern[str]] | None = None) -> list[str]:
    """Return the internal names in the entry files as `<path>:<line>: <kind>: <spelling>`."""
    return [
        f"{name}:{number}: {found}"
        for name in entry_files(root)
        for number, line in enumerate(
            (root / name).read_text(encoding="utf-8", errors="replace").splitlines(), start=1
        )
        for found in internal_names_in(line, patterns)
    ]


#: Fake card and request ids (not real ids). The spelling is split so this file itself is not matched by the id-shape detection.
FAKE_CARD_ID = "ki-" + "0123456789ab"
FAKE_REQUEST_ID = "lt-" + "0123456789ABCDEFGHJKMNPQRS"
#: Example node names standing in for the ones a site passes in MACHINE_NAMES_ENV.
EXAMPLE_PATTERNS = name_patterns(("node-h", "node-f"))


def test_each_kind_finds_its_names() -> None:
    assert internal_names_in("see agora-redesign") == ["application system name: agora"]
    assert internal_names_in("(raise (AcpProtocolError msg))") == ["application system name: Acp"]
    assert internal_names_in("a Kanban card and the hypha view") == ["application system name: Kanban", "application system name: hypha"]
    assert internal_names_in(f"card {FAKE_CARD_ID}") == [f"work board and conversation id: {FAKE_CARD_ID}"]
    assert internal_names_in(f"request {FAKE_REQUEST_ID}") == [f"work board and conversation id: {FAKE_REQUEST_ID}"]
    assert internal_names_in("image: node-h:5000/doeff-worker on node-f", EXAMPLE_PATTERNS) == [
        "cluster node name: node-h",
        "cluster node name: node-f",
    ]
    assert internal_names_in("cd /home/someone/repos && ls /Users/someone") == [
        "personal home path: /home/someone",
        "personal home path: /Users/someone",
    ]


def test_general_words_and_public_names_are_not_internal_names() -> None:
    # Words entry files use with a general meaning, public names, spellings with letters before or after, and URL paths do not match.
    spared = [
        "the herdr backend keeps one conversation per session",
        "a time budget for each test and the build artifact",
        "https://github.com/proboscis/doeff",
        "Atlassian Jira, the macp tool, node-hx",
        "GET https://api.example.com/users/42",
        "a 12-character id ab-4064da9af9eb and ~/project/README.md",
    ]
    assert [text for text in spared if internal_names_in(text, EXAMPLE_PATTERNS)] == []


def test_node_names_come_from_the_environment() -> None:
    # The tree names no node: without the variable the kind is absent, and the site's names are read comma-separated.
    assert machine_names_from({}) == ()
    assert "cluster node name" not in name_patterns(())
    assert machine_names_from({MACHINE_NAMES_ENV: "node-h, node-f,,"}) == ("node-h", "node-f")


def test_the_scan_reads_the_entry_files_and_finds_a_planted_name(tmp_path: Path) -> None:
    # Outside entry files (other docs pages, package src, GitHub workflows) is not read. Cache dirs under examples/ are not read.
    for name in (
        "README.md",
        "docs/index.md",
        "docs/README.md",
        "CONTRIBUTING.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/config.yml",
        ".github/workflows/ci.yml",
        "docs/history/notes.md",
        "packages/doeff-a/README.md",
        "packages/doeff-a/src/agora.py",
        "examples/demo.py",
        "examples/nested/demo.hy",
        "examples/__pycache__/demo.cpython-314.pyc",
        "examples/.ipynb_checkpoints/demo.py",
    ):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("# node-h\n", encoding="utf-8")
    assert entry_files(tmp_path) == [
        "README.md",
        "docs/index.md",
        "docs/README.md",
        "CONTRIBUTING.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/config.yml",
        "packages/doeff-a/README.md",
        "examples/demo.py",
        "examples/nested/demo.hy",
    ]
    for name in entry_files(tmp_path):
        (tmp_path / name).write_text("# doeff\n", encoding="utf-8")
    assert internal_names_in_entry_files(tmp_path, EXAMPLE_PATTERNS) == []
    (tmp_path / "packages/doeff-a/README.md").write_text("# doeff-a\n\nRuns on node-h.\n", encoding="utf-8")
    assert internal_names_in_entry_files(tmp_path, EXAMPLE_PATTERNS) == ["packages/doeff-a/README.md:3: cluster node name: node-h"]


def test_the_scan_covers_the_entry_files_of_this_repo() -> None:
    # Guard against going green on an empty population: all fixed entry files exist, and the issue templates, component READMEs and files under examples/ are included.
    scanned = entry_files(REPO_ROOT)
    assert [name for name in FIXED_ENTRY_FILES if name not in scanned] == []
    for name in (".github/ISSUE_TEMPLATE/bug_report.yml", ".github/ISSUE_TEMPLATE/question.yml"):
        assert name in scanned
    for name in ("packages/doeff-vm/README.md", "packages/doeff-hy/README.md", "packages/doeff-cluster/README.md"):
        assert name in scanned
    assert any(name.startswith("examples/") for name in scanned)


def test_no_entry_file_has_an_internal_name() -> None:
    found = internal_names_in_entry_files(REPO_ROOT)
    assert found == [], (
        "an entry file contains an internal name — rewrite it in general words (the word list and the explanation are in "
        "INTERNAL_NAMES in tests/test_entry_files_have_no_internal_names.py):\n" + "\n".join(found)
    )
