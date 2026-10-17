"""doeff run says the real import error of the user's module, not a missing attribute of its package.

Before: ``--program myapp.main.program`` where ``myapp/main.py`` imports a dependency that is not
installed ended at ``Error: module 'myapp' has no attribute 'main'``. ``import_symbol`` tries the
longest module path first and used to treat every ImportError / AttributeError raised while
importing as "this is not a module path, try a shorter one", so the error of the user's own
module was swallowed and the walk ended at the package (card ki-90c36aee939d: the same hid why
tests/cli/cli_assets.py cannot be imported).

Now only "the module (or a package above it) does not exist" falls back to a shorter path; an
error raised by the code of a module that does exist is the answer. The ``module:name`` form
already said it, so both forms are compared. ``--env`` names go through
``StandardSymbolLoader``, which had its own copy of the loop.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from doeff import __main__ as cli
from doeff.cli.discovery import StandardSymbolLoader
from doeff.cli.run_services import import_symbol

pytestmark = pytest.mark.cli

_PKG = "importerrpkg"
_MISSING_DEP = "doeff_test_dependency_that_is_not_installed"

_PROGRAM = """
from doeff import do


@do
def main():
    return "ok"


program = main()
"""


@pytest.fixture
def user_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A package ``importerrpkg`` on sys.path whose modules fail to import in different ways."""
    package = tmp_path / _PKG
    package.mkdir()
    trace = tmp_path / "trace.txt"
    (package / "__init__.py").write_text("")
    (package / "ok.py").write_text(_PROGRAM)
    (package / "needs_missing_dep.py").write_text(
        f"open({str(trace)!r}, 'a').write('x')\nimport {_MISSING_DEP}\n{_PROGRAM}"
    )
    (package / "helper.py").write_text(f"import {_MISSING_DEP}\n")
    (package / "needs_helper.py").write_text(f"from . import helper\n{_PROGRAM}")
    (package / "calls_missing_function.py").write_text(f"import os\nos.no_such_function()\n{_PROGRAM}")
    (package / "imports_missing_name.py").write_text(
        f"from {_PKG} import name_it_does_not_have\n{_PROGRAM}"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    before = set(sys.modules)
    yield tmp_path
    for name in set(sys.modules) - before:
        del sys.modules[name]


def _run_cli(*args: str) -> int:
    return cli.main(["run", "--no-runbox", *args])


def test_missing_dependency_of_the_program_module_is_named(user_package, capsys) -> None:
    exit_code = _run_cli("--program", f"{_PKG}.needs_missing_dep.program")

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err.splitlines() == [f"Error: No module named '{_MISSING_DEP}'"]


def test_colon_form_names_the_same_missing_dependency(user_package, capsys) -> None:
    exit_code = _run_cli("--program", f"{_PKG}.needs_missing_dep:program")

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err.splitlines() == [f"Error: No module named '{_MISSING_DEP}'"]


def test_missing_dependency_two_imports_deep_is_named(user_package, capsys) -> None:
    exit_code = _run_cli("--program", f"{_PKG}.needs_helper.program")

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err.splitlines() == [f"Error: No module named '{_MISSING_DEP}'"]


def test_attribute_error_raised_by_the_program_module_is_not_a_missing_module(
    user_package, capsys
) -> None:
    exit_code = _run_cli("--program", f"{_PKG}.calls_missing_function.program")

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err.splitlines() == ["Error: module 'os' has no attribute 'no_such_function'"]


def test_import_error_raised_by_the_program_module_is_named(user_package, capsys) -> None:
    exit_code = _run_cli("--program", f"{_PKG}.imports_missing_name.program")

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err.splitlines()[0].startswith(
        f"Error: cannot import name 'name_it_does_not_have' from '{_PKG}'"
    )


def test_missing_dependency_is_reported_in_json(user_package, capsys) -> None:
    exit_code = _run_cli("--program", f"{_PKG}.needs_missing_dep.program", "--format", "json")

    captured = capsys.readouterr()
    assert exit_code == 1
    payload = json.loads(captured.out)
    assert payload["status"] == "error"
    assert payload["error"] == "ModuleNotFoundError"
    assert payload["message"] == f"No module named '{_MISSING_DEP}'"


def test_a_module_that_fails_to_import_runs_its_code_once(user_package, capsys) -> None:
    _run_cli("--program", f"{_PKG}.needs_missing_dep.program")

    capsys.readouterr()
    assert (user_package / "trace.txt").read_text() == "x"


def test_env_module_with_a_missing_dependency_is_named(user_package, capsys) -> None:
    exit_code = _run_cli(
        "--program", f"{_PKG}.ok.program",
        "--env", f"{_PKG}.needs_missing_dep.program",
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    # --env prints its deprecation notice first; the error is the last line.
    assert captured.err.splitlines()[-1] == f"Error: No module named '{_MISSING_DEP}'"


def test_import_symbol_raises_the_error_of_the_module_that_exists(user_package) -> None:
    with pytest.raises(ModuleNotFoundError) as caught:
        import_symbol(f"{_PKG}.needs_missing_dep.program")

    assert caught.value.name == _MISSING_DEP


def test_symbol_loader_raises_the_error_of_the_module_that_exists(user_package) -> None:
    with pytest.raises(ModuleNotFoundError) as caught:
        StandardSymbolLoader().load_symbol(f"{_PKG}.needs_missing_dep.program")

    assert caught.value.name == _MISSING_DEP


def test_module_that_does_not_exist_still_says_which_module(user_package, capsys) -> None:
    exit_code = _run_cli("--program", "importerr_no_such_package.main.program")

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err.splitlines() == ["Error: No module named 'importerr_no_such_package'"]


def test_submodule_that_does_not_exist_is_a_missing_attribute_of_its_package(
    user_package, capsys
) -> None:
    exit_code = _run_cli("--program", f"{_PKG}.no_such_module.program")

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err.splitlines()[0] == f"Error: module '{_PKG}' has no attribute 'no_such_module'"


def test_program_of_a_module_that_imports_runs(user_package, capsys) -> None:
    exit_code = _run_cli("--program", f"{_PKG}.ok.program", "--format", "json")

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert json.loads(captured.out)["result"] == "ok"
