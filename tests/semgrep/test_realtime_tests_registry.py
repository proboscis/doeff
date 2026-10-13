"""realtime の名簿(tests/realtime_tests.toml)と、規則 doeff-no-sleep-in-tests の外へ出る道の突き合わせ(card ki-94380dba532f・ki-6aa7cb5ffb83)。

.semgrep.yaml の doeff-no-sleep-in-tests は realtime を名乗る検の中の sleep を鳴らさない。名乗ってよいのは実時間そのものが
対象の検だけなので、名乗る検を名簿に理由と一緒に載せ、名簿と marker が 1 対 1 で合う事をここで見る — 名簿に無い marker も、
marker の無い名簿の行も赤。数えるのは規則の範囲(名が tests の dir の下の .py と、どこに在っても検の名の .py。規則の検体
tests/semgrep/fixtures/ と名簿の [suites] の dir を除く)の、関数・method の @pytest.mark.realtime と module の段の
pytestmark(単独・list・tuple)。

規則の外へ出る道が名簿の 2 つの表だけである事も見る: 規則の paths.exclude は名簿の [suites] と 1 対 1 で、規則の範囲には
規則を外す行の注釈が 1 行も無い(規則の名を挙げた注釈も、名を挙げずに全部の規則を外す注釈も赤)。
"""

from __future__ import annotations

import ast
import fnmatch
import functools
import os
import re
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any

import tomllib
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY = REPO_ROOT / "tests" / "realtime_tests.toml"
SEMGREP_CONFIG = REPO_ROOT / ".semgrep.yaml"
RULE = "doeff-no-sleep-in-tests"
FIXTURES = "tests/semgrep/fixtures"
# 規則の paths.include と同じ範囲(test_the_rule_reaches_what_this_scan_reads が 2 つを突き合わせる)
REACH_DIR = "tests"
REACH_NAMES = ("test_*.py", "*_test.py", "conftest.py")
# 歩かない dir = 隠しの dir(.git・.venv)と build の出力
NOT_WALKED = (".*", "node_modules", "target", "__pycache__", "build", "dist")
# 行の注釈の 2 つの綴り。語をつなげて作る — この file も規則の範囲に在り、下の見本の行が自分の走査に当たらないようにする
SWITCH_OFF_SPELLINGS = ("nosem" + "grep", "no" + "sem")
_SWITCH_OFF = re.compile(rf"#.*\b(?:{'|'.join(SWITCH_OFF_SPELLINGS)})\b(?P<names>.*)")


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


def switched_off_lines(source: str) -> list[int]:
    """The lines of ``source`` whose comment takes the rule off: it names the rule, or names no rule at all."""
    return [
        number
        for number, line in enumerate(source.splitlines(), start=1)
        if (found := _SWITCH_OFF.search(line))
        and (RULE in found["names"] or not found["names"].strip(" :"))
    ]


def in_reach(relative: str) -> bool:
    """Whether the rule's paths.include reaches the file at ``relative`` (a path from the repo root)."""
    path = PurePosixPath(relative)
    return path.suffix == ".py" and (
        REACH_DIR in path.parts[:-1]
        or any(fnmatch.fnmatchcase(path.name, name) for name in REACH_NAMES)
    )


def _is_under(relative: str, directory: str) -> bool:
    return relative.startswith(f"{directory}/")


@functools.cache
def _registry() -> dict[str, dict[str, str]]:
    return tomllib.loads(REGISTRY.read_text(encoding="utf-8"))


@functools.cache
def _rule_paths() -> dict[str, Any]:
    rules = yaml.safe_load(SEMGREP_CONFIG.read_text(encoding="utf-8"))["rules"]
    [rule] = [rule for rule in rules if rule["id"] == RULE]
    return rule["paths"]


@functools.cache
def _watched_sources() -> dict[str, str]:
    """規則が見張る .py(範囲から検体と [suites] の dir を除いた物)の、repo の根からの path → 中身。"""
    outside = (FIXTURES, *_registry()["suites"])
    sources: dict[str, str] = {}
    for current, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [
            name
            for name in dirs
            if not any(fnmatch.fnmatchcase(name, pattern) for pattern in NOT_WALKED)
        ]
        for name in files:
            relative = (Path(current) / name).relative_to(REPO_ROOT).as_posix()
            if in_reach(relative) and not any(
                _is_under(relative, directory) for directory in outside
            ):
                sources[relative] = (REPO_ROOT / relative).read_text(encoding="utf-8")
    return sources


def test_registry_lists_exactly_the_realtime_marks() -> None:
    declared = {
        mark
        for relative, source in _watched_sources().items()
        if "realtime" in source
        for mark in realtime_marks(source, relative)
    }
    registry = set(_registry()["tests"])
    assert sorted(declared - registry) == [], (
        "realtime marks missing from tests/realtime_tests.toml"
    )
    assert sorted(registry - declared) == [], (
        "tests/realtime_tests.toml lists tests without the mark"
    )


def test_every_registry_entry_gives_a_reason() -> None:
    assert [
        f"[{table}] {key}"
        for table in ("tests", "suites")
        for key, reason in _registry()[table].items()
        if not reason.strip()
    ] == []


def test_the_rule_reaches_what_this_scan_reads() -> None:
    assert _rule_paths()["include"] == [f"{REACH_DIR}/", *REACH_NAMES]


def test_the_rule_excludes_exactly_the_registered_suites() -> None:
    excluded = sorted(path.rstrip("/") for path in _rule_paths().get("exclude", []))
    assert excluded == sorted(_registry()["suites"]), (
        "the rule's paths.exclude and [suites] of tests/realtime_tests.toml differ"
    )
    assert [suite for suite in excluded if not (REPO_ROOT / suite).is_dir()] == []


def test_no_comment_takes_the_rule_off() -> None:
    switched_off = [
        f"{relative}:{line}"
        for relative, source in _watched_sources().items()
        if any(spelling in source for spelling in SWITCH_OFF_SPELLINGS)
        for line in switched_off_lines(source)
    ]
    assert switched_off == [], (
        f"{RULE} is taken off by a line comment — declare realtime and list the test in "
        "tests/realtime_tests.toml, or wait for the event"
    )


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


def test_the_scan_reads_every_switch_off_form() -> None:
    long, short = SWITCH_OFF_SPELLINGS
    source = (
        "import time\n"
        f"time.sleep(1)  # {long}: {RULE}\n"
        f"time.sleep(1)  # {short}: another-rule, {RULE}\n"
        f"time.sleep(1)  # {long}\n"
        f"# {long}\n"
        "time.sleep(1)\n"
        f"time.sleep(1)  # {long}: another-rule\n"
        f'TEXT = "{long} in a string is not a comment"\n'
        "time.sleep(1)  # a plain comment\n"
    )
    assert switched_off_lines(source) == [2, 3, 4, 5]


def test_the_scan_reads_the_reach_of_the_rule() -> None:
    reached = (
        "tests/test_a.py",
        "tests/helper.py",
        "packages/doeff-x/tests/deep/support.py",
        "packages/doeff-x/suite/test_b.py",
        "packages/doeff-x/suite/b_test.py",
        "packages/doeff-x/suite/conftest.py",
    )
    not_reached = (
        "packages/doeff-x/suite/harness.py",
        "packages/doeff-x/src/doeff_x/tests.py",
        "tests/test_a.hy",
    )
    assert [path for path in reached if not in_reach(path)] == []
    assert [path for path in not_reached if in_reach(path)] == []
