from __future__ import annotations

import re
import warnings
from pathlib import Path

from doeff import Get, Put, Tell, do, run
from doeff_core_effects.handlers import reader, state, writer


REPO_ROOT = Path(__file__).resolve().parents[1]

ARCHIVAL_DOC_DIRS = {
    Path("docs/proposals"),
    Path("docs/specs"),
}
ARCHIVAL_DOC_FILES = {
    Path("docs/MILESTONES.md"),
}
BANNED_PROMOTION = re.compile(
    r"\b(prefer|preferred|composed explicitly|installed with|current-doc|"
    r"reader-facing docs now use)\b.*\bWithHandler\b",
    re.IGNORECASE,
)


def _current_doc_paths() -> list[Path]:
    docs = [REPO_ROOT / "README.md"]
    docs.extend(sorted((REPO_ROOT / "docs").rglob("*.md")))
    current_docs: list[Path] = []
    for path in docs:
        rel = path.relative_to(REPO_ROOT)
        if rel in ARCHIVAL_DOC_FILES:
            continue
        if any(rel.is_relative_to(archival) for archival in ARCHIVAL_DOC_DIRS):
            continue
        current_docs.append(path)
    return current_docs


def test_current_docs_do_not_show_deprecated_withhandler_call_snippets() -> None:
    violations: list[str] = []
    for path in _current_doc_paths():
        rel = path.relative_to(REPO_ROOT)
        for line_no, line in enumerate(path.read_text().splitlines(), start=1):
            if "WithHandler(" in line:
                violations.append(f"{rel}:{line_no}: {line.strip()}")
            elif BANNED_PROMOTION.search(line):
                violations.append(f"{rel}:{line_no}: {line.strip()}")

    assert violations == []


def test_core_handler_factories_compose_directly_without_withhandler_warning() -> None:
    @do
    def counter_program():
        yield Put("counter", 0)
        yield Tell("Starting computation")
        count = yield Get("counter")
        yield Put("counter", count + 1)
        return count + 1

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", DeprecationWarning)
        program = counter_program()
        program = writer()(program)
        program = state()(program)
        program = reader(env={"greeting": "hello"})(program)
        result = run(program)

    deprecations = [
        warning for warning in captured if issubclass(warning.category, DeprecationWarning)
    ]
    assert deprecations == []
    assert result == 1
