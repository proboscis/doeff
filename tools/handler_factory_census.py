"""List the handler factories that take no parameters, in the code doeff ships (ADR-DOE-API-002).

A handler with nothing to set is a value, not a factory: ``await_handler``, not ``await_handler()``.
A factory without parameters is right only when each call builds a handler with its own new state
(``in_memory_cache_handler()`` — a fresh store per call). This tool finds every factory without
parameters, and ``tests/data/zero_parameter_handler_factories.json`` says for each why it stays:

- ``呼びごとの状態``: each call builds a handler over a new store (the reason is written per row);
- ``直す``: no settings and no per-call state — to become a handler value (the card that does it);
- ``factory でない``: the reading took it for a factory, and it is not one (the reason per row).

The deftest of ADR-DOE-API-002 compares the two: a factory found and not in the table is red, and
a row of the table no longer found is red too (the table only shrinks with the code).

What is read (git-tracked files, outside the tests): ``doeff/``, ``packages/`` and ``examples/``,
less every ``tests`` dir and ``examples/testing_without_mocks`` (it runs daily against the doeff on
PyPI). A Python module-level ``def`` without any parameter is a factory when its return type names a
handler (``…Handler``), when a ``return`` calls a handler builder (``handler``, ``stacked_handlers``,
``_named_handler`` …), or when it returns a ``def (effect, k)`` it defines under ``@handler`` or
``@do``. A Hy ``(defn name [] …)`` (``#^ type`` before the name too) is a factory when the name says
``handler`` or ``progress``, its return type names a handler, or its body builds one (``defhandler``, ``(handler …)``, ``stacked-handlers``). ``defk`` is a program,
not a factory, and is not read.

    uv run python tools/handler_factory_census.py           # the rows found, one per line
    uv run python tools/handler_factory_census.py --check   # compare with the table (exit 1 if not equal)
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TABLE = ROOT / "tests" / "data" / "zero_parameter_handler_factories.json"
VERDICTS = ("呼びごとの状態", "直す", "factory でない")

_READ_DIRS = ("doeff/", "packages/", "examples/")
_SKIPPED_DIRS = ("examples/testing_without_mocks/",)
_BUILDERS = frozenset(
    {
        "handler",
        "_program_handler",
        "_named_handler",
        "_former_factory_handler",
        "_former_factory_stack",
        "stacked_handlers",
        "_rust_handler_installer",
        "_boundary",
    }
)
_HY_DEFN = re.compile(
    r"^\(defn\s+(?:\[[^\]]*\]\s+)?(?:#\^\s*(?P<returns>\S+)\s+)?(?P<name>[\w\-!?*<>]+)\s+\[\s*\]",
    re.MULTILINE,
)
_HY_BUILDS = re.compile(r"\(defhandler\b|\(handler\s|stacked-handlers")
_HY_NEXT_TOP_FORM = re.compile(r"^\(", re.MULTILINE)


@dataclass(frozen=True)
class Factory:
    """One factory without parameters: where it is defined and its name."""

    path: str
    name: str

    @property
    def key(self) -> str:
        """The row key of the known table: ``path::name``."""
        return f"{self.path}::{self.name}"


def _tracked_sources(root: Path) -> list[str]:
    """The git-tracked Python and Hy files of the shipped code (the dirs the census reads)."""
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--", "*.py", "*.hy"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [
        path
        for path in listed
        if path.startswith(_READ_DIRS)
        and not path.startswith(_SKIPPED_DIRS)
        and "tests" not in Path(path).parts
    ]


def _takes_nothing(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether the def has no parameter at all (a default-only parameter is a setting)."""
    arguments = function.args
    return not (
        arguments.posonlyargs
        or arguments.args
        or arguments.kwonlyargs
        or arguments.vararg
        or arguments.kwarg
    )


def _returns_a_handler(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether the def reads as building a handler: its return type, a builder call, or an inner def (effect, k)."""
    if function.returns is not None and re.search(r"Handler\b", ast.unparse(function.returns)):
        return True
    inner_handlers = {
        node.name
        for node in function.body
        if isinstance(node, ast.FunctionDef)
        and len(node.args.args) == 2
        and any(ast.unparse(decorator) in {"handler", "do"} for decorator in node.decorator_list)
    }
    for node in ast.walk(function):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        value = node.value
        if isinstance(value, ast.Call) and ast.unparse(value.func).split(".")[-1] in _BUILDERS:
            return True
        if isinstance(value, ast.Name) and value.id in inner_handlers:
            return True
    return False


def _python_factories(path: str, source: str) -> list[Factory]:
    """The module-level Python defs of one file that read as handler factories without parameters."""
    tree = ast.parse(source, filename=path)
    return [
        Factory(path, node.name)
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _takes_nothing(node)
        and _returns_a_handler(node)
    ]


def _hy_body(source: str, match: re.Match[str]) -> str:
    """The text of a top-level Hy form after its head, up to the next top-level form (to read what it builds)."""
    following = _HY_NEXT_TOP_FORM.search(source, match.end())
    return source[match.end() : following.start() if following else len(source)]


def _hy_factories(path: str, source: str) -> list[Factory]:
    """The Hy ``(defn name [] …)`` of one file that read as handler factories."""
    return [
        Factory(path, match["name"])
        for match in _HY_DEFN.finditer(source)
        if "handler" in match["name"]
        or "progress" in match["name"]
        or "Handler" in (match["returns"] or "")
        or _HY_BUILDS.search(_hy_body(source, match))
    ]


def _factories_of(root: Path, path: str) -> list[Factory]:
    """The factories without parameters one tracked file defines."""
    source = (root / path).read_text(encoding="utf-8")
    return _python_factories(path, source) if path.endswith(".py") else _hy_factories(path, source)


def census(root: Path = ROOT) -> list[Factory]:
    """Every handler factory without parameters in the code doeff ships, in path order."""
    found = [factory for path in _tracked_sources(root) for factory in _factories_of(root, path)]
    return sorted(found, key=lambda factory: factory.key)


def read_table(table: Path = TABLE) -> dict[str, dict[str, str]]:
    """The known table: the key ``path::name`` of each row, with its verdict and reason."""
    rows = json.loads(table.read_text(encoding="utf-8"))["rows"]
    for row in rows:
        if row["verdict"] not in VERDICTS:
            raise ValueError(f"{row['key']}: verdict {row['verdict']!r} is not one of {VERDICTS}")
        if not row.get("why"):
            raise ValueError(f"{row['key']}: a row says why it stays (why)")
    return {row["key"]: row for row in rows}


def differences(root: Path = ROOT, table: Path = TABLE) -> list[str]:
    """What the census and the table disagree on: factories not in the table, rows not found."""
    found = {factory.key for factory in census(root)}
    known = read_table(table)
    unlisted = [
        f"{key}: a handler factory without parameters, not in the table — make the handler a value "
        "(ADR-DOE-API-002 R1), or add a row saying why each call needs its own handler"
        for key in sorted(found - known.keys())
    ]
    gone = [
        f"{key}: in the table, no longer found — remove its row"
        for key in sorted(known.keys() - found)
    ]
    return unlisted + gone


def main(argv: list[str] | None = None) -> int:
    """List the factories with the table's verdicts, or check the two agree (``--check``)."""
    parser = argparse.ArgumentParser(
        description="引数の無い handler の factory の一覧(ADR-DOE-API-002)"
    )
    parser.add_argument("--check", action="store_true", help="既知の表と突き合わせる")
    arguments = parser.parse_args(argv)
    if arguments.check:
        problems = differences()
        print("\n".join(problems) if problems else "census and table agree")
        return 1 if problems else 0
    known = read_table()
    for factory in census():
        row = known.get(factory.key)
        verdict = "(表に無い)" if row is None else f"{row['verdict']} — {row['why']}"
        print(f"{factory.key}\t{verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
