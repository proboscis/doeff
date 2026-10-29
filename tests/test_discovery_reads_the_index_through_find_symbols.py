"""doeff run の discovery(doeff/cli/discovery.py)は、doeff-indexer の effect FindSymbols で索引を引く program で、doeff run は
それを handler rust_indexer(Rust の索引器)の下で回す(ADR-DOE-API-001・依頼書 lt-S4R44DQ5ZPJZD986TY812Q2DW9 の答え
lt-ZDN45RSNPV16BKSM997GGZ9CVB・card ki-1e51e218527e)。前は class Indexer を直に呼ぶ class IndexerBasedDiscovery だった。

- Rust の索引器の下で、印の付いた interpreter のうち program の module の階層で最も近い物と、階層の印の付いた env を根から選ぶ。
- doeff run の resolve_context が同じ program で既定の interpreter と env を決める(--env が在れば env は探さない)。
- FindSymbols に test の handler で答えれば、Rust の索引器も file も無しに同じ program が走る。
- 索引を作れない失敗は program を通って run の外へ出る(doeff run はそれを記録して既定の interpreter へ落ちる)。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Literal

import pytest
from doeff_indexer import FindSymbols, SymbolInfo, rust_indexer

from doeff import K, Transfer, TransferThrow, handler, run
from doeff.cli.discovery import Discovered, discover, find_default_interpreter
from doeff.cli.run_services import RunContext, resolve_context

PROGRAM = "myapp.features.auth.login.login_program"

#: 印の付いた interpreter と env の小さな project(env は型の注釈が要る — 注釈の無い変数は索引器が読まない)。
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
    # 階層の外の印は選ばない。
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
    """PROJECT を書いた dir を今の dir にし(Rust の索引器は今の dir の下を読む)、import の path に足す。"""
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
    """FindSymbols に INDEX の表で答える(Rust の索引器と同じく、tags を全部持ち、種類の合う行)。"""
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


def test_an_index_that_cannot_be_built_fails_the_discovery_program() -> None:
    with pytest.raises(RuntimeError, match=re.escape("cannot index myapp.features.auth.login")):
        run(broken_index(discover(PROGRAM)))
