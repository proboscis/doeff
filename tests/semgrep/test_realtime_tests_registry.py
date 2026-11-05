"""Cross-check of the realtime registry (tests/realtime_tests.toml) against the ways out of the test-sleep rule.

doeff-no-sleep-in-tests in .semgrep.yaml does not flag sleep inside tests that declare realtime. Only tests whose subject is wall-clock
time itself may declare it, so those tests are listed in the registry with a reason, and this file checks that the registry and the markers match one to one: a marker not in the registry,
or a registry row without a marker, is red. What is counted is, within the rule's scope (.py files under a tests dir, and .py files named like tests anywhere; excluding the rule's fixtures
tests/semgrep/fixtures/ and the [suites] dirs of the registry), the @pytest.mark.realtime on functions and methods and the module-level
pytestmark (single, list, or tuple).

It also checks that the registry's two tables are the only ways out of the rule: the rule's paths.exclude matches the registry's [suites] one to one, and the rule's scope
has no line annotation that switches the rule off (an annotation naming the rule, or one switching off all rules without naming it, is red).

Semgrep cannot read Hy tests, so the doeff-linter rule DOEFF211 holds the same policy (packages/doeff-linter/src/project/test_sleep.rs).
A Hy declaration takes one of three forms: a module-top-level ``(val pytestmark pytest.mark.realtime)``, the deftest meta
``{:marks ["realtime"]}``, or the decorator of a plain test function ``(defn [pytest.mark.realtime] name [] ...)``. Here they are counted with the same reader that doeff-hy uses to
read pytest marks (``record_module_binding`` and ``_extract-test-meta``). The scope is read from the root pyproject.toml ``[tool.doeff-linter.test_sleep]`` (the same single place as the rule), and
it checks that the dir lines of that exclude are limited to "the rule's fixture dirs and the registry's [suites]". The file lines of the exclude (independent scripts that a test launches as a separate
process; they do not import pytest, so they cannot declare the marker) stand in for the declaration and match the registry's [tests] rows one to one. That the rule and this reader read the same forms as declarations is checked by both reading the same fixtures
in packages/doeff-linter/tests/fixtures/test_sleep/ (the rule side is packages/doeff-linter/tests/test_sleep.rs).
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

import hy
import tomllib
import yaml
from doeff_hy._pytest_items import ModuleMarks, decode_records, record_module_binding
from doeff_hy.macros import _extract_test_meta

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY = REPO_ROOT / "tests" / "realtime_tests.toml"
SEMGREP_CONFIG = REPO_ROOT / ".semgrep.yaml"
RULE = "doeff-no-sleep-in-tests"
FIXTURES = "tests/semgrep/fixtures"
# Same scope as the rule's paths.include (test_the_rule_reaches_what_this_scan_reads compares the two)
REACH_DIR = "tests"
REACH_NAMES = ("test_*.py", "*_test.py", "conftest.py")
# Dirs not walked = hidden dirs (.git, .venv) and build output
NOT_WALKED = (".*", "node_modules", "target", "__pycache__", "build", "dist")
# The two spellings of a line annotation. Built by joining words, because this file is also in the rule's scope and the sample lines below must not match its own scan
SWITCH_OFF_SPELLINGS = ("nosem" + "grep", "no" + "sem")
_SWITCH_OFF = re.compile(rf"#.*\b(?:{'|'.join(SWITCH_OFF_SPELLINGS)})\b(?P<names>.*)")
# Declaration of the scope of the Hy test rule (DOEFF211 of doeff-linter): reads the same single place as the rule
PYPROJECT = REPO_ROOT / "pyproject.toml"
HY_SUFFIXES = (".hy", ".hyk", ".hyp")
MARK = "realtime"
# Of the dir lines in DOEFF211's exclude, those that are not the registry's [suites] = the rule's fixture dirs (their job is to hold hits; they are not run)
HY_RULE_SAMPLES = ("packages/doeff-linter/tests/fixtures", FIXTURES)
# Shared fixtures that confirm the rule (Rust) and this reader (Python) read the same forms as declarations
HY_SAMPLES = REPO_ROOT / "packages" / "doeff-linter" / "tests" / "fixtures" / "test_sleep"
HY_DECLARES = "; 名乗る"


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


def _recorded_marks(head: str, args: list[object]) -> tuple[str, ...]:
    """The pytest marks doeff-hy records for the binding ``(head *args)``: the names of ``(val pytestmark …)``, else none."""
    recorded = record_module_binding(head, args)
    if recorded is None:
        return ()
    # The trailing string of the record expression (do (import ...) (record-at-import (globals) "<record JSON>")) is the record
    [record] = decode_records([str(recorded[-1][-1])])
    return record.names if isinstance(record, ModuleMarks) else ()


def _is_realtime_mark(form: object) -> bool:
    """Whether ``form`` is the mark ``pytest.mark.realtime`` (or a call of it), read the way a ``pytestmark`` value is."""
    return MARK in _recorded_marks("val", [hy.models.Symbol("pytestmark"), form])


def _function_name(parts: list[object]) -> str:
    """The name a ``defn`` binds: the first symbol after the decorators, ``:async`` and the annotation."""
    for part in parts:
        if isinstance(part, hy.models.Symbol):
            return str(part)
        if isinstance(part, hy.models.Expression) and str(part[0]) == "annotate":
            return str(part[1])
    raise ValueError(f"defn without a name: {parts!r}")


def _realtime_tests(form: object) -> Iterator[str]:
    """The names (as pytest shows them) of the tests under ``form`` that declare realtime: a deftest by its meta map, a plain function by its decorators."""
    if not isinstance(form, hy.models.Sequence) or isinstance(
        form, (hy.models.FString, hy.models.FComponent)
    ):
        return
    parts = list(form)
    head = str(parts[0]) if parts and isinstance(parts[0], hy.models.Symbol) else ""
    if isinstance(form, hy.models.Expression) and head in ("quote", "quasiquote"):
        return
    if isinstance(form, hy.models.Expression) and head == "deftest" and len(parts) > 1:
        body = parts[3:] if len(parts) > 2 and isinstance(parts[2], hy.models.List) else parts[2:]
        marks = _extract_test_meta(body)[3] or []
        if any(
            isinstance(mark, (hy.models.String, hy.models.Symbol)) and str(mark) == MARK
            for mark in marks
        ):
            yield hy.mangle(str(parts[1]))
            return
    decorated = (
        isinstance(form, hy.models.Expression) and head in ("defn", "defn/a") and len(parts) > 2
    )
    if (
        decorated
        and isinstance(parts[1], hy.models.List)
        and any(_is_realtime_mark(mark) for mark in parts[1])
    ):
        yield hy.mangle(_function_name(parts[2:]))
        return
    for part in parts:
        yield from _realtime_tests(part)


def hy_realtime_marks(source: str, key: str) -> set[str]:
    """The registry keys that the Hy ``source`` (the file named ``key``) declares realtime."""
    forms = list(hy.read_many(source, filename=key))
    module = any(
        MARK in _recorded_marks(str(form[0]), list(form[1:]))
        for form in forms
        if isinstance(form, hy.models.Expression)
        and len(form) > 0
        and isinstance(form[0], hy.models.Symbol)
    )
    return {
        *([key] if module else []),
        *(f"{key}::{name}" for form in forms for name in _realtime_tests(form)),
    }


@functools.cache
def _linter_reach() -> dict[str, list[str]]:
    """The root pyproject.toml [tool.doeff-linter.test_sleep] (the population of DOEFF211: parts, names, exclude)."""
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["doeff-linter"][
        "test_sleep"
    ]


def hy_scripts_named_by_the_rule() -> set[str]:
    """Hy scripts that the exclude of DOEFF211 names by file (they stand in for the declaration; same shape as the keys of the registry's [tests])."""
    return {
        place for place in _linter_reach()["exclude"] if PurePosixPath(place).suffix in HY_SUFFIXES
    }


def hy_in_reach(relative: str) -> bool:
    """Whether DOEFF211 judges the Hy file at ``relative`` (the predicate is_test_sleep_file of doeff-linter)."""
    path = PurePosixPath(relative)
    reach = _linter_reach()
    return (
        path.suffix in HY_SUFFIXES
        and (
            any(part in reach["parts"] for part in path.parts[:-1])
            or any(fnmatch.fnmatchcase(path.name, name) for name in reach["names"])
        )
        and not any(relative == place or _is_under(relative, place) for place in reach["exclude"])
    )


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
def _walked() -> tuple[str, ...]:
    """Paths of the repo's files (excluding hidden dirs and build output), relative to the repo root."""
    found: list[str] = []
    for current, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [
            name
            for name in dirs
            if not any(fnmatch.fnmatchcase(name, pattern) for pattern in NOT_WALKED)
        ]
        found.extend((Path(current) / name).relative_to(REPO_ROOT).as_posix() for name in files)
    return tuple(found)


@functools.cache
def _watched_sources() -> dict[str, str]:
    """The .py files the rule watches (the scope minus the fixtures and the [suites] dirs): path relative to the repo root -> contents."""
    outside = (FIXTURES, *_registry()["suites"])
    return {
        relative: (REPO_ROOT / relative).read_text(encoding="utf-8")
        for relative in _walked()
        if in_reach(relative) and not any(_is_under(relative, directory) for directory in outside)
    }


@functools.cache
def _watched_hy_sources() -> dict[str, str]:
    """The Hy files DOEFF211 watches (the population of the config section): path relative to the repo root -> contents."""
    return {
        relative: (REPO_ROOT / relative).read_text(encoding="utf-8")
        for relative in _walked()
        if hy_in_reach(relative)
    }


def test_registry_lists_exactly_the_realtime_marks() -> None:
    declared = (
        {
            mark
            for relative, source in _watched_sources().items()
            if MARK in source
            for mark in realtime_marks(source, relative)
        }
        | {
            mark
            for relative, source in _watched_hy_sources().items()
            if MARK in source
            for mark in hy_realtime_marks(source, relative)
        }
        | hy_scripts_named_by_the_rule()
    )
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


def test_the_linter_excludes_exactly_the_registered_suites_and_its_own_samples() -> None:
    scripts = hy_scripts_named_by_the_rule()
    excluded = sorted(set(_linter_reach()["exclude"]) - scripts)
    assert excluded == sorted({*_registry()["suites"], *HY_RULE_SAMPLES}), (
        "the dirs in exclude of [tool.doeff-linter.test_sleep] differ from [suites] of "
        "tests/realtime_tests.toml plus the rule's sample dirs"
    )
    assert [place for place in excluded if not (REPO_ROOT / place).is_dir()] == []
    assert [script for script in sorted(scripts) if not (REPO_ROOT / script).is_file()] == []


def test_a_script_the_linter_names_does_not_also_declare() -> None:
    # Naming by file is only for scripts that cannot declare the marker; do not use it for files that can (tests and tools that import pytest).
    assert [
        script
        for script in sorted(hy_scripts_named_by_the_rule())
        if "pytest" in (REPO_ROOT / script).read_text(encoding="utf-8")
    ] == []


def test_the_linter_reach_holds_hy_tests() -> None:
    watched = _watched_hy_sources()
    assert len(watched) > 400, "the reach of DOEFF211 lost most of the Hy tests"
    assert [
        sample for sample in HY_RULE_SAMPLES if any(_is_under(path, sample) for path in watched)
    ] == []


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


def _declared_in_sample(source: str, key: str) -> set[str]:
    """Registry keys of the tests defined by the declaration-marker lines of a fixture (deftest or plain function)."""
    heads = re.findall(
        rf"^\((?:deftest|defn) (.*){re.escape(HY_DECLARES)}$", source, flags=re.MULTILINE
    )
    return {f"{key}::{hy.mangle(re.search(r'test-[^ ]+', head)[0])}" for head in heads}


def test_the_hy_scan_reads_the_declarations_the_rule_excuses() -> None:
    source = (HY_SAMPLES / "sleeps.hy").read_text(encoding="utf-8")
    declared = _declared_in_sample(source, "tests/sample.hy")
    assert len(declared) == 4, "the sample lost its marked tests"
    assert hy_realtime_marks(source, "tests/sample.hy") == declared
    module = (HY_SAMPLES / "realtime_module.hy").read_text(encoding="utf-8")
    assert hy_realtime_marks(module, "tests/rig.hy") == {"tests/rig.hy"}


def test_the_hy_scan_reads_every_declaration_form() -> None:
    source = (
        "(import pytest)\n"
        "(val pytestmark #(pytest.mark.slow (pytest.mark.realtime)))\n"
        '(deftest test-marked {:marks ["realtime"]} (assert True))\n'
        '(deftest test-with-params [tmp-path] "doc" {:marks #(slow realtime)} (assert True))\n'
        "(when (available)\n"
        '  (deftest test-nested {:marks ["realtime"]} (assert True)))\n'
        "(defn [pytest.mark.slow pytest.mark.realtime] #^ None test-plain-function [] (assert True))\n"
        "(defn [pytest.mark.slow] test-other-decorator [] (assert True))\n"
        "(defn test-mark-in-the-parameters [pytest.mark.realtime] (assert True))\n"
        '(deftest test-other-mark {:marks ["slow"]} (assert True))\n'
        "(deftest test-plain (assert True))\n"
        '\'(deftest test-quoted {:marks ["realtime"]} (assert True))\n'
    )
    assert hy_realtime_marks(source, "tests/sample.hy") == {
        "tests/sample.hy",
        "tests/sample.hy::test_marked",
        "tests/sample.hy::test_with_params",
        "tests/sample.hy::test_nested",
        "tests/sample.hy::test_plain_function",
    }
    undeclared = (
        "(import pytest)\n"
        "(setv pytestmark pytest.mark.realtime)\n"
        "(val pytestmark pytest.mark.slow)\n"
        "(defn setup [] (val pytestmark pytest.mark.realtime))\n"
    )
    assert hy_realtime_marks(undeclared, "tests/sample.hy") == set()


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


def test_the_scan_reads_the_reach_of_the_hy_rule() -> None:
    reached = (
        "tests/test_a.hy",
        "tests/rig.hy",
        "packages/doeff-x/tests/deep/support.hy",
        "packages/doeff-x/tests/fixtures/job.hy",
        "packages/doeff-x/suite/test_b.hy",
        "packages/doeff-x/suite/b_test.hy",
    )
    not_reached = (
        "packages/doeff-x/suite/harness.hy",
        "packages/doeff-x/src/doeff_x/tests.hy",
        "tests/test_a.py",
        "packages/doeff-linter/tests/fixtures/test_sleep/sleeps.hy",
        "tests/semgrep/fixtures/sample.hy",
    )
    assert [path for path in reached if not hy_in_reach(path)] == []
    assert [path for path in not_reached if hy_in_reach(path)] == []
