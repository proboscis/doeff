"""Check that the types of public names are known to the type tools (gap 6 of the type report).

pyright's verifytypes counts, for each public name of a package, whether the types of all arguments, returns, and attributes are known. Measured in an environment with only the 4 core
packages, doeff was 81.9% and doeff_core_effects was 87.5% (2026-10-17, main 9fe8c330b, pyright 1.1.414). The strict type check of a user
who uses an unknown name raises an Unknown error. There were 3 causes:

- Names inside the scheduler (the ids of ``Task``, ``Future``, ``Promise``, ``Semaphore``, ``ExternalPromise.fail``, the fields of
  ``SchedulerDeadlockError``) and the ``__repr__`` of effects had no annotation, so the whole class was "partially unknown".
  ``Tell`` and ``slog`` were unknown through their return classes (``WriterTellEffect``, ``SlogEffect``).
- Top-level handlers (such as ``writer``) were variables without annotations, so the inferred type could differ by tool (ambiguous).
- Placeholders for removed names (17 of them, such as ``KleisliProgram``) were visible as values of the untyped ``_Removed``. The placeholders were
  hidden from type tools: flagging an error on the import line tells the user sooner than a type check letting through a name that always fails when used (the same as names doeff does not have --
  tests/test_unknown_doeff_import_static_types.py). The runtime import and the guidance text do not change.

After the fix, doeff 99.5% and doeff_core_effects 92.3% (same environment and tool).

Here verifytypes is run in the development environment, and it is verified that the public names whose types are unknown are only those whose reason is written in the table below. It also goes red when a name in the table
gets a type -- remove it from the table to widen the protected range (Some, Maybe, and Nothing of doeff.result got types from the card for gap 5 and
left this table).

Failure cases: red when adding an unannotated function, attribute, or variable to a public module; removing the annotation of the id of ``Task``; or exposing the placeholders to type tools.
"""

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from doeff_hy._static_check import python_without_bytecode

#: ``python -m doeff demo`` (doeff/cli/demo.py) prints its own source and runs it in the same style as the README examples (examples without annotations).
#: Adding annotations would make the printed program and handlers diverge from the README examples.
_DEMO_PRINTS_THE_README_EXAMPLE = "an example in the README style (no annotations) that demo prints"

#: Public names of doeff whose types remain unknown, and the reason.
_DOEFF_GAPS: dict[str, str] = {
    "doeff.cli.demo.digest": _DEMO_PRINTS_THE_README_EXAMPLE,
    "doeff.cli.demo.canned": _DEMO_PRINTS_THE_README_EXAMPLE,
    "doeff.cli.demo.trace": _DEMO_PRINTS_THE_README_EXAMPLE,
    "doeff.cli.demo.budget": _DEMO_PRINTS_THE_README_EXAMPLE,
    "doeff.cli.demo.live_world": _DEMO_PRINTS_THE_README_EXAMPLE,
}

#: The aiohttp types themselves are "partially unknown" to verifytypes (the yarl ``URL`` and ``BasicAuth`` in the arguments of ``ClientSession``,
#: the fields of ``web.Request`` and ``web.StreamResponse``). All name annotations are written (the types are known with
#: ``--ignoreexternal``, which drops the type report of external packages), and it cannot be fixed on the doeff side. The strict type check does not count the fields of external classes,
#: so users get no Unknown error.
_AIOHTTP_TYPES_ARE_PARTIALLY_UNKNOWN = "aiohttp types are partially unknown to verifytypes (all annotations are present; external package)"

#: Top-level names of doeff_core_effects whose types remain unknown, and the reason (when adding, write the reason with it).
_CORE_HEAD_GAPS: dict[str, str] = {
    "doeff_core_effects.WebEdge": _AIOHTTP_TYPES_ARE_PARTIALLY_UNKNOWN,
    "doeff_core_effects.aiohttp_ws_link_handler": _AIOHTTP_TYPES_ARE_PARTIALLY_UNKNOWN,
}
#: The doeff_core_effects modules all of whose public names have known types (the range this test protects). For now all are counted through the hidden modules below.
_CORE_TYPED_MODULES: tuple[str, ...] = ()
#: Of the protected range, the modules hidden with a _ prefix on the name (ADR-DOE-API-001 R5).
#: verifytypes does not count names of _ modules, so they are counted by the names the entry point exports from these modules (such as ``doeff_core_effects.Task``).
#: The entry point's class fields do not appear as separate rows and are folded into the type-known status (isTypeKnown) of the class row.
_CORE_TYPED_HIDDEN_MODULES = (
    "doeff_core_effects._effects",
    "doeff_core_effects._scheduler",
    "doeff_core_effects._handlers",
    "doeff_core_effects._storage",
    "doeff_core_effects._cache_effects",
    "doeff_core_effects._cache_handlers",
    "doeff_core_effects._memo_effects",
    "doeff_core_effects._memo_handlers",
)
_CORE_ENTRY = (
    Path(__file__).resolve().parents[1]
    / "packages/doeff-core-effects/doeff_core_effects/__init__.py"
)
#: Placeholders for removed names (they exist only at runtime and name the replacement when used).
_REMOVED = {
    "Delegate": "use 'yield effect' to re-perform in handler body",
    "KleisliProgram": "use @do instead",
    "default_handlers": "compose handlers by calling handler(program)",
    "async_run": "use run() with scheduled()",
}


def _verifytypes(directory: Path, package: str) -> list[dict]:
    """The public-name rows of ``pyright --verifytypes <package>``. Run from a dir outside the repo's pyrightconfig.json (which resolves the package as local
    source via extraPaths -- verifytypes cannot find an installed package), pointing at the development environment's python.

    verifytypes launches the python that is asked for the search path not with ``--pythonpath`` but with `-I` from ``python`` on PATH (measured on 1.1.411 --
    passing the wrapper only via ``--pythonpath`` wrote the site startup .pyc into the checkout). So the `-B` wrapper is also placed at the head of PATH."""
    (directory / "pyrightconfig.json").write_text("{}", encoding="utf-8")
    bin_dir = directory / "bin"
    bin_dir.mkdir()
    python = python_without_bytecode(bin_dir, sys.executable)
    for name in ("python", "python3"):
        (bin_dir / name).symlink_to(python)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("PATH", str(bin_dir), prepend=os.pathsep)
        result = subprocess.run(
            [sys.executable, "-m", "pyright", "--project", str(directory / "pyrightconfig.json"),
             "--pythonpath", str(python), "--verifytypes", package, "--outputjson"],
            cwd=directory, capture_output=True, text=True, timeout=180, check=False,
        )
    report = json.loads(result.stdout)
    completeness = report["typeCompleteness"]
    assert completeness["packageRootDirectory"], f"verifytypes did not find {package}: {report}"
    return [symbol for symbol in completeness["symbols"] if symbol["isExported"]]


def _unknown(symbols: list[dict]) -> set[str]:
    return {symbol["name"] for symbol in symbols if not symbol["isTypeKnown"]}


@pytest.fixture(scope="module")
def doeff_symbols(tmp_path_factory: pytest.TempPathFactory) -> list[dict]:
    return _verifytypes(tmp_path_factory.mktemp("verifytypes-doeff"), "doeff")


@pytest.fixture(scope="module")
def core_symbols(tmp_path_factory: pytest.TempPathFactory) -> list[dict]:
    return _verifytypes(tmp_path_factory.mktemp("verifytypes-core"), "doeff_core_effects")


def test_every_public_name_of_doeff_has_a_known_type_but_the_listed_gaps(doeff_symbols: list[dict]) -> None:
    unlisted = sorted(_unknown(doeff_symbols) - set(_DOEFF_GAPS))
    assert unlisted == [], f"public names with unknown types -- add annotations: {unlisted}"


def test_each_listed_gap_of_doeff_is_still_a_gap(doeff_symbols: list[dict]) -> None:
    closed = sorted(set(_DOEFF_GAPS) - _unknown(doeff_symbols))
    assert closed == [], f"types are now known -- remove from the table and bring into the protected range: {closed}"


def test_the_scheduler_handles_and_tell_and_slog_have_known_types(core_symbols: list[dict]) -> None:
    known = {symbol["name"] for symbol in core_symbols if symbol["isTypeKnown"]}
    named = {
        f"doeff_core_effects.{name}"
        for name in ("Task", "Future", "Promise", "Semaphore", "ExternalPromise", "SchedulerDeadlockError",
                     "Gather", "Race", "Wait", "Cancel", "Tell", "slog", "WriterTellEffect", "SlogEffect", "writer")
    }
    assert sorted(named - known) == []


def _in_module(name: str, modules: tuple[str, ...]) -> bool:
    return any(name == module or name.startswith(f"{module}.") for module in modules)


def _entry_names_by_hidden_module() -> dict[str, set[str]]:
    """For each hidden module, the names the entry point exports with ``from <that module> import X as X``.

    Both names bound immediately and names under ``if TYPE_CHECKING:``.
    """
    tree = ast.parse(_CORE_ENTRY.read_text(encoding="utf-8"))
    imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    return {
        module: {
            alias.asname or alias.name
            for node in imports
            if _in_module(node.module or "", (module,))
            for alias in node.names
        }
        for module in _CORE_TYPED_HIDDEN_MODULES
    }


def test_every_public_name_of_the_typed_core_modules_has_a_known_type(core_symbols: list[dict]) -> None:
    """The protected range is verified by a set of names, not a lower bound on the count. The previous lower bound (more than 100) dates from when the class fields of public modules were
    counted one row at a time. After modules were hidden with _, only the entry point's names became rows (fields are folded into the class row), and the count dropped each time
    names moved inward (96 -> 91: TraceSink, VmWork, Resumption, and PRIORITY_* removed from the entry point). So verify that every module in the table
    exports names at the entry point, that verifytypes reports all of the entry point's names, and that all of them have known types."""
    by_module = _entry_names_by_hidden_module()
    silent = sorted(module for module, names in by_module.items() if not names)
    assert silent == [], f"hidden modules whose entry point exports no names -- review the table and the entry point: {silent}"
    entry_names = {f"doeff_core_effects.{name}" for names in by_module.values() for name in names}
    reported = {symbol["name"] for symbol in core_symbols}
    assert sorted(entry_names - reported) == [], "entry-point names that verifytypes does not report -- they leak out of the protected range"
    covered = [
        symbol for symbol in core_symbols
        if _in_module(symbol["name"], (*_CORE_TYPED_MODULES, *entry_names))
    ]
    assert sorted(_unknown(covered)) == []


def test_every_head_name_of_doeff_core_effects_has_a_known_type_but_the_listed_gaps(
    core_symbols: list[dict],
) -> None:
    head = [symbol for symbol in core_symbols if symbol["name"].count(".") == 1]
    unknown = _unknown(head)
    assert sorted(unknown - set(_CORE_HEAD_GAPS)) == [], "top-level names with unknown types -- add annotations"
    assert sorted(set(_CORE_HEAD_GAPS) - unknown) == [], "types are now known -- remove from the table and bring into the protected range"


def test_type_checkers_do_not_see_the_removed_names(doeff_symbols: list[dict]) -> None:
    exported = {symbol["name"] for symbol in doeff_symbols}
    assert sorted(f"doeff.{name}" for name in _REMOVED if f"doeff.{name}" in exported) == []


@pytest.mark.parametrize(("name", "replacement"), sorted(_REMOVED.items()))
def test_a_removed_name_still_imports_and_names_its_replacement_on_use(name: str, replacement: str) -> None:
    import doeff

    removed = getattr(doeff, name)
    with pytest.raises(RuntimeError, match=f"^{re.escape(f'{name} was removed: {replacement}')}$"):
        removed()
