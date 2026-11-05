"""Test that the docs table of contents separates pages readable with `pip install doeff` alone from pages that need components outside the core installed separately
(doeff-publish initiative).

`pip install doeff` installs only the 5 core components (CORE in tools/verify_release_install.py); doeff-agents, doeff-llm and others
are not installed. The previous table of contents listed pages that need those components (the agent guide, the Gemini page, etc.) in the same section as core pages, so
first-time readers copied them in a core-only environment and hit ModuleNotFoundError. From the names of the components that a page's fences import, this test reads
the following 3 things (fences are not run — running is done by the README test and the getting-started path test):

1. A page listed in the section "Pages That Need Other Components" is not linked from elsewhere in the table of contents.
2. If a page linked by a row of "Table of Contents" imports a component outside the core, that row names the component.
3. A page listed in the section "Pages That Need Other Components" says in its head (before the first `## `) that components outside `pip install doeff`
   are needed, and names all the non-core components it imports.
"""

from __future__ import annotations

import ast
import re
from itertools import takewhile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "docs" / "index.md"
TABLE_OF_CONTENTS = "## Table of Contents"
COMPONENT_PAGES = "## Pages That Need Other Components"
# The phrase that the head of every page in the section "Pages That Need Other Components" must contain.
HEAD_NOTE = "outside `pip install doeff`"
LINK = re.compile(r"\]\(([^)#\s]+\.md)(?:#[^)]*)?\)")
FENCE = re.compile(r"^```(\w+)[^\n]*\n(.*?)^```", flags=re.MULTILINE | re.DOTALL)
PYTHON_IMPORT = re.compile(r"^\s*(?:from|import)\s+(doeff\w*)", flags=re.MULTILINE)
HY_IMPORT = re.compile(r"\((?:import|require)\s+(doeff\w*)")


def _core_modules() -> frozenset[str]:
    """Import names of the 5 core components (from the literal CORE in tools/verify_release_install.py)."""
    tree = ast.parse((ROOT / "tools" / "verify_release_install.py").read_text(encoding="utf-8"))
    core = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and [target.id for target in node.targets if isinstance(target, ast.Name)] == ["CORE"]
    )
    return frozenset(name.replace("-", "_") for name in core)


def _section(lines: list[str], heading: str) -> range:
    """Line numbers from the line after a table-of-contents `## ` heading to just before the next `## ` heading."""
    start = lines.index(heading)
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    return range(start + 1, end)


def _page(link: str) -> Path:
    return (INDEX.parent / link).resolve()


def _links(lines: list[str]) -> list[Path]:
    return [_page(link) for line in lines for link in LINK.findall(line)]


def _imports(language: str, body: str) -> list[str]:
    """Top-level names of the doeff modules imported by one fence (python and Hy fences only)."""
    if language == "python":
        return PYTHON_IMPORT.findall(body)
    if language == "hy":
        return HY_IMPORT.findall(body)
    return []


def _outside_components(page: Path) -> set[str]:
    """Names of the non-core doeff components imported by the page's python and Hy fences (in the form `doeff-agents`)."""
    fences = FENCE.findall(page.read_text(encoding="utf-8"))
    modules = {module for language, body in fences for module in _imports(language, body)}
    return {module.replace("_", "-") for module in modules - _core_modules()}


def _head(page: Path) -> str:
    """The head of a page — from the first line (title) to just before the first `## ` heading."""
    lines = page.read_text(encoding="utf-8").splitlines()
    return "\n".join(takewhile(lambda line: not line.startswith("## "), lines))


INDEX_LINES = INDEX.read_text(encoding="utf-8").splitlines()
COMPONENT_SECTION = _section(INDEX_LINES, COMPONENT_PAGES)
COMPONENT_LINKS = _links([INDEX_LINES[index] for index in COMPONENT_SECTION])
TOC_LINES = [
    INDEX_LINES[index]
    for index in _section(INDEX_LINES, TABLE_OF_CONTENTS)
    if LINK.search(INDEX_LINES[index])
]


def _id(page: Path) -> str:
    return str(page.relative_to(ROOT))


def test_component_pages_are_linked_only_from_their_section() -> None:
    outside = [line for index, line in enumerate(INDEX_LINES) if index not in COMPONENT_SECTION]
    assert COMPONENT_LINKS, f"{COMPONENT_PAGES} has no page"
    assert sorted(_id(page) for page in set(COMPONENT_LINKS) & set(_links(outside))) == []


@pytest.mark.parametrize("line", TOC_LINES, ids=lambda line: LINK.findall(line)[0])
def test_table_of_contents_line_names_the_outside_components_its_page_imports(line: str) -> None:
    for page in _links([line]):
        missing = {name for name in _outside_components(page) if f"`{name}`" not in line}
        assert missing == set(), f"components not named by the row of {_id(page)}: {sorted(missing)}"


@pytest.mark.parametrize("page", COMPONENT_LINKS, ids=_id)
def test_component_page_names_its_components_at_the_top(page: Path) -> None:
    head = _head(page)
    assert HEAD_NOTE in head, f"the head of {_id(page)} has no note \"{HEAD_NOTE}\""
    missing = {name for name in _outside_components(page) if f"`{name}`" not in head}
    assert missing == set(), f"components not named in the head of {_id(page)}: {sorted(missing)}"
