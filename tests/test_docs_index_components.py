"""docs の目次が、`pip install doeff` だけで読める page と、核の外の部品を別に入れる page を分けて見せる事の検
(取り組み doeff-publish・card ki-d20475e94d87)。

`pip install doeff` が入れるのは核の 5 個(tools/verify_release_install.py の CORE)だけで、doeff-agents・doeff-llm などは
入らない。前の目次は、それらの部品が要る page(agent の手引き・Gemini の page など)を核の page と同じ節に並べていたので、
初めての読み手は核だけの環境で写して ModuleNotFoundError で落ちた。この検は、page の囲みが import する部品の名から
次の 3 つを読む(囲みは走らせない — 走らせるのは README の検と入門の道の検):

1. 節「Pages That Need Other Components」に載る page は、目次の他の所から link されない。
2. 「Table of Contents」の行が link する page が核の外の部品を import するなら、その行が部品の名を書く。
3. 節「Pages That Need Other Components」に載る page は、頭(最初の `## ` の前)で `pip install doeff` の外の部品が
   要る事を言い、import する核の外の部品の名を全部書く。
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
# 節「Pages That Need Other Components」の page の頭が必ず書く句。
HEAD_NOTE = "outside `pip install doeff`"
LINK = re.compile(r"\]\(([^)#\s]+\.md)(?:#[^)]*)?\)")
FENCE = re.compile(r"^```(\w+)[^\n]*\n(.*?)^```", flags=re.MULTILINE | re.DOTALL)
PYTHON_IMPORT = re.compile(r"^\s*(?:from|import)\s+(doeff\w*)", flags=re.MULTILINE)
HY_IMPORT = re.compile(r"\((?:import|require)\s+(doeff\w*)")


def _core_modules() -> frozenset[str]:
    """核の 5 個の import の名(tools/verify_release_install.py の CORE の字面から)。"""
    tree = ast.parse((ROOT / "tools" / "verify_release_install.py").read_text(encoding="utf-8"))
    core = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and [target.id for target in node.targets if isinstance(target, ast.Name)] == ["CORE"]
    )
    return frozenset(name.replace("-", "_") for name in core)


def _section(lines: list[str], heading: str) -> range:
    """目次の `## ` の見出しの次の行から、次の `## ` の見出しの前までの行の番号。"""
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
    """囲み 1 つが import する doeff の module の頭の名(python と Hy の囲みだけ)。"""
    if language == "python":
        return PYTHON_IMPORT.findall(body)
    if language == "hy":
        return HY_IMPORT.findall(body)
    return []


def _outside_components(page: Path) -> set[str]:
    """page の python と Hy の囲みが import する、核の外の doeff の部品の名(`doeff-agents` の形)。"""
    fences = FENCE.findall(page.read_text(encoding="utf-8"))
    modules = {module for language, body in fences for module in _imports(language, body)}
    return {module.replace("_", "-") for module in modules - _core_modules()}


def _head(page: Path) -> str:
    """page の頭 — 最初の行(題)から最初の `## ` の見出しの前まで。"""
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
    assert COMPONENT_LINKS, f"{COMPONENT_PAGES} に page が無い"
    assert sorted(_id(page) for page in set(COMPONENT_LINKS) & set(_links(outside))) == []


@pytest.mark.parametrize("line", TOC_LINES, ids=lambda line: LINK.findall(line)[0])
def test_table_of_contents_line_names_the_outside_components_its_page_imports(line: str) -> None:
    for page in _links([line]):
        missing = {name for name in _outside_components(page) if f"`{name}`" not in line}
        assert missing == set(), f"{_id(page)} の行が名を書かない部品: {sorted(missing)}"


@pytest.mark.parametrize("page", COMPONENT_LINKS, ids=_id)
def test_component_page_names_its_components_at_the_top(page: Path) -> None:
    head = _head(page)
    assert HEAD_NOTE in head, f"{_id(page)} の頭に「{HEAD_NOTE}」の注記が無い"
    missing = {name for name in _outside_components(page) if f"`{name}`" not in head}
    assert missing == set(), f"{_id(page)} の頭が名を書かない部品: {sorted(missing)}"
