"""realtime の名簿(tests/realtime_tests.toml)と、検が名乗る realtime の marker の突き合わせ(card ki-94380dba532f)。

.semgrep.yaml の doeff-no-sleep-in-tests は realtime を名乗る検の中の sleep を鳴らさない。名乗ってよいのは実時間そのものが
対象の検だけなので、名乗る検を名簿に理由と一緒に載せ、名簿と marker が 1 対 1 で合う事をここで見る — 名簿に無い marker も、
marker の無い名簿の行も赤。数えるのは tests/ と packages/*/tests/ の .py(規則の検体 tests/semgrep/fixtures/ を除く)の、
関数・method の @pytest.mark.realtime と module の段の pytestmark(単独・list・tuple)。
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import tomllib

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY = REPO_ROOT / "tests" / "realtime_tests.toml"
FIXTURES = REPO_ROOT / "tests" / "semgrep" / "fixtures"


def _is_realtime(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "realtime"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
    )


def _declares_pytestmark(statement: ast.stmt) -> bool:
    return isinstance(statement, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id == "pytestmark" for target in statement.targets
    )


def _module_marks(tree: ast.Module, key: str) -> Iterator[str]:
    values = (
        value
        for statement in tree.body
        if isinstance(statement, ast.Assign) and _declares_pytestmark(statement)
        for value in (
            statement.value.elts
            if isinstance(statement.value, (ast.List, ast.Tuple))
            else (statement.value,)
        )
    )
    if any(_is_realtime(value) for value in values):
        yield key


def _function_marks(body: list[ast.stmt], prefix: str) -> Iterator[str]:
    for node in body:
        if isinstance(node, ast.ClassDef):
            yield from _function_marks(node.body, f"{prefix}::{node.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
            _is_realtime(decorator) for decorator in node.decorator_list
        ):
            yield f"{prefix}::{node.name}"


def realtime_marks(source: str, key: str) -> set[str]:
    """The registry keys that ``source`` (the file named ``key``) declares realtime."""
    tree = ast.parse(source)
    return {*_module_marks(tree, key), *_function_marks(tree.body, key)}


def _test_files() -> list[Path]:
    roots = (REPO_ROOT / "tests", *sorted((REPO_ROOT / "packages").glob("*/tests")))
    return sorted(
        path
        for root in roots
        for path in root.rglob("*.py")
        if not path.is_relative_to(FIXTURES)
    )


def _registry() -> dict[str, str]:
    return tomllib.loads(REGISTRY.read_text())["tests"]


def test_registry_lists_exactly_the_realtime_marks() -> None:
    declared = {
        mark
        for path in _test_files()
        if "realtime" in (source := path.read_text())
        for mark in realtime_marks(source, path.relative_to(REPO_ROOT).as_posix())
    }
    registry = set(_registry())
    assert sorted(declared - registry) == [], "realtime marks missing from tests/realtime_tests.toml"
    assert sorted(registry - declared) == [], "tests/realtime_tests.toml lists tests without the mark"


def test_every_registry_entry_gives_a_reason() -> None:
    assert [key for key, reason in _registry().items() if not reason.strip()] == []


def test_the_scan_reads_every_declaration_form() -> None:
    source = (
        "import pytest\n"
        "pytestmark = [pytest.mark.slow, pytest.mark.realtime]\n"
        "@pytest.mark.realtime\n"
        "def test_function():\n"
        "    pass\n"
        "class TestGroup:\n"
        "    @pytest.mark.timeout(5)\n"
        "    @pytest.mark.realtime\n"
        "    async def test_method(self):\n"
        "        pass\n"
        "@pytest.mark.slow\n"
        "def test_other_mark():\n"
        "    pass\n"
    )
    assert realtime_marks(source, "tests/sample.py") == {
        "tests/sample.py",
        "tests/sample.py::test_function",
        "tests/sample.py::TestGroup::test_method",
    }
