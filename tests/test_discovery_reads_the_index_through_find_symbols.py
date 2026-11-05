"""doeff run's discovery (doeff/cli/discovery.py) is a program that queries the index with doeff-indexer's FindSymbols effect, and doeff run
runs it under the handler rust_indexer (the Rust indexer) (ADR-DOE-API-001). Previously it was class IndexerBasedDiscovery calling class Indexer directly.

- Under the Rust indexer, pick the marked interpreter closest in the program module's hierarchy, and pick the hierarchy-marked envs from the root.
- doeff run's resolve_context decides the default interpreter and env with the same program (if --env is given, envs are not searched).
- If FindSymbols is answered by a test handler, the same program runs without the Rust indexer, the extension, or any files.
- A failure to build the index is caught inside the program (discover), reported as a warning, and nothing is searched (doeff run falls back to the default interpreter).
- If doeff-indexer is absent, silently do not search; if it is present but the extension cannot be loaded, emit a warning and do not search.
"""

from __future__ import annotations

import importlib
import logging
import re
import sys
from pathlib import Path
from typing import Literal

import pytest
from doeff_indexer import FindSymbols, SymbolInfo, rust_indexer

from doeff import K, Transfer, TransferThrow, handler, run
from doeff.cli.discovery import Discovered, discover, find_default_interpreter
from doeff.cli.run_services import RunContext, _discover, resolve_context

PROGRAM = "myapp.features.auth.login.login_program"

#: A small project with marked interpreters and envs (envs need type annotations — the indexer does not read unannotated variables).
PROJECT = {
    "myapp/__init__.py": (
        "# doeff: interpreter, default\n"
        "def base_interpreter(program):\n"
        "    return program\n"
        "\n"
        "\n"
        "# doeff: default\n"
        "base_env: dict = {'timeout': 10}\n"
    ),
    "myapp/features/__init__.py": "# doeff: default\nfeatures_env: dict = {'flag': True}\n",
    "myapp/features/auth/__init__.py": (
        "# doeff: interpreter, default\n"
        "def auth_interpreter(program):\n"
        "    return program\n"
        "\n"
        "\n"
        "# doeff: default\n"
        "auth_env: dict = {'auth': 'oauth2'}\n"
    ),
    "myapp/features/auth/login.py": "def login_program():\n    return 'login'\n",
    # Marks outside the hierarchy are not chosen.
    "other/__init__.py": (
        "# doeff: interpreter, default\n"
        "def other_interpreter(program):\n"
        "    return program\n"
        "\n"
        "\n"
        "# doeff: default\n"
        "other_env: dict = {}\n"
    ),
}

FOUND = Discovered(
    interpreter="myapp.features.auth.auth_interpreter",
    envs=("myapp.base_env", "myapp.features.features_env", "myapp.features.auth.auth_env"),
)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Make the dir where PROJECT was written the current dir (the Rust indexer reads under the current dir) and add it to the import path."""
    for relative, text in PROJECT.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in [
        name for name in sys.modules if name == "myapp" or name.startswith(("myapp.", "other"))
    ]:
        monkeypatch.delitem(sys.modules, name)
    return tmp_path


def test_the_rust_indexer_finds_the_closest_interpreter_and_the_envs_root_first(
    project: Path,
) -> None:
    assert run(rust_indexer(discover(PROGRAM))) == FOUND


def test_doeff_run_resolves_the_default_interpreter_and_envs_through_the_effect(
    project: Path,
) -> None:
    resolved = resolve_context(
        RunContext(program_path=PROGRAM, program_instance=None, interpreter_path=None)
    )
    assert (resolved.interpreter_path, resolved.env_paths) == (FOUND.interpreter, list(FOUND.envs))
    named = resolve_context(
        RunContext(
            program_path=PROGRAM,
            program_instance=None,
            interpreter_path=None,
            env_paths=["myapp.base_env"],
        )
    )
    assert (named.interpreter_path, named.env_paths) == (FOUND.interpreter, ["myapp.base_env"])


def _symbol(
    full_path: str, symbol_type: Literal["function", "variable"], tags: tuple[str, ...]
) -> SymbolInfo:
    module_path, _, name = full_path.rpartition(".")
    return SymbolInfo(
        name=name,
        module_path=module_path,
        full_path=full_path,
        symbol_type=symbol_type,
        tags=tags,
        line_number=1,
        file_path=f"{module_path.replace('.', '/')}.py",
    )


INDEX = (
    _symbol("myapp.base_interpreter", "function", ("interpreter", "default")),
    _symbol("myapp.features.auth.auth_interpreter", "function", ("interpreter", "default")),
    _symbol("other.other_interpreter", "function", ("interpreter", "default")),
    _symbol("myapp.base_env", "variable", ("default",)),
    _symbol("myapp.features.auth.auth_env", "variable", ("default",)),
)


@handler
def fake_index(effect: FindSymbols, k: K):
    """Answer FindSymbols from the INDEX table (like the Rust indexer: rows that have all the tags and match the kind)."""
    rows = tuple(
        row
        for row in INDEX
        if set(effect.tags) <= set(row.tags) and effect.symbol_type in (None, row.symbol_type)
    )
    return (yield Transfer(k, rows))


def test_a_test_handler_answers_find_symbols_without_the_rust_indexer() -> None:
    assert run(fake_index(discover(PROGRAM))) == Discovered(
        "myapp.features.auth.auth_interpreter", ("myapp.base_env", "myapp.features.auth.auth_env")
    )
    assert run(fake_index(discover(PROGRAM, envs=False))) == Discovered(
        "myapp.features.auth.auth_interpreter", ()
    )
    assert run(fake_index(find_default_interpreter("unmarked.module.program"))) is None


@handler
def broken_index(effect: FindSymbols, k: K):
    return (yield TransferThrow(k, RuntimeError(f"cannot index {effect.module_path}")))


def test_an_index_that_cannot_be_built_is_logged_and_discovers_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The failure is caught in one place inside the program (discover) — run never sees the exception, and doeff run falls back to the default interpreter."""
    with caplog.at_level(logging.WARNING, logger="doeff.cli.discovery"):
        assert run(broken_index(discover(PROGRAM))) == Discovered(None, ())
    assert "cannot index myapp.features.auth.login" in caplog.text


def _forget_doeff_indexer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the already-loaded inner modules of doeff_indexer be re-imported only during this test (restored afterwards).

    `from doeff_indexer import _native` looks at the package attribute first, so remove the package attribute together with sys.modules.
    """
    package = sys.modules["doeff_indexer"]
    for name in [name for name in sys.modules if name.startswith("doeff_indexer.")]:
        monkeypatch.delitem(sys.modules, name)
        monkeypatch.delattr(package, name.rpartition(".")[2], raising=False)


def test_the_effect_and_its_answer_type_are_read_without_the_rust_extension(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """FindSymbols and SymbolInfo can be read without the extension .so (a test handler can answer), and only rust_indexer loads the extension.
    When doeff-indexer is present but the extension cannot be loaded, doeff run does not stay silent: it emits a warning and does not search (finding A of the configuration review)."""
    _forget_doeff_indexer(monkeypatch)
    monkeypatch.setitem(
        sys.modules, "doeff_indexer._native", None
    )  # importing raises ModuleNotFoundError
    effects = importlib.import_module("doeff_indexer._effects")
    assert effects.FindSymbols("myapp").module_path == "myapp"
    with pytest.raises(ImportError, match=re.escape("doeff_indexer._native")):
        importlib.import_module("doeff_indexer._handlers")
    with caplog.at_level(logging.WARNING, logger="doeff.cli.run_services"):
        assert _discover(PROGRAM, envs=True) is None
    assert "doeff-indexer cannot be read" in caplog.text


def test_without_doeff_indexer_doeff_run_discovers_nothing_quietly(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """If doeff-indexer is not installed, discovery is optional, so do not search and emit no warning."""
    _forget_doeff_indexer(monkeypatch)
    monkeypatch.setitem(sys.modules, "doeff_indexer", None)
    with caplog.at_level(logging.WARNING):
        assert _discover(PROGRAM, envs=True) is None
    assert not caplog.records
