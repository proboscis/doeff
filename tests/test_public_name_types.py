"""公開の名の型が型の道具に分かる事の検(型の報告の抜け 6・card ki-23e84023ab9c)。

pyright の verifytypes は、package の公開の名ごとに、引数と戻りと属性の型がすべて分かるかを数える。核の 4 個だけの環境で
測ると、doeff は 81.9%・doeff_core_effects は 87.5% だった(2026-10-17・main 9fe8c330b・pyright 1.1.414)。分からない名を
使う人の strict の型の check は Unknown の error を出す。訳は 3 つだった:

- scheduler の内の名(``Task``・``Future``・``Promise``・``Semaphore`` の id、``ExternalPromise.fail``、
  ``SchedulerDeadlockError`` の欄)と effect の ``__repr__`` に注記が無く、class ごと「一部分からない」になっていた。
  ``Tell`` と ``slog`` は戻りの class(``WriterTellEffect``・``SlogEffect``)を通して分からなかった。
- 頭の handler(``writer`` など)が注記の無い変数で、道具によって推す型が違いうる(ambiguous)。
- 廃止した名の置き物(``KleisliProgram`` など 17 個)が、型の無い ``_Removed`` の値として見えていた。置き物は型の道具から
  隠した: 使えば必ず落ちる名を型の check が通すより、import の行で error にする方が早く知らせる(doeff に無い名と同じ —
  tests/test_unknown_doeff_import_static_types.py)。実行時の import と案内の文は変わらない。

直した後は doeff 99.5%・doeff_core_effects 92.3%(同じ環境と道具)。

ここでは開発の環境で verifytypes を走らせ、型の分からない公開の名が、下の表に訳を書いた物だけである事を確かめる。表の名に
型が付いた時も赤にする — 表から外して、守る範囲を広げる(doeff.result の Some・Maybe・Nothing は抜け 5 の card が型を付け、
この表から外れた)。

失敗ケース: 公開の module に注記の無い関数・属性・変数を足す・``Task`` の id の注記を外す・置き物を型の道具に見せる、で赤。
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

#: ``python -m doeff demo``(doeff/cli/demo.py)は自分の source を README の例と同じ書き方(注記を書かない例)のまま
#: 印字して走らせる。注記を足すと、印字する program と handler が README の例とずれる。
_DEMO_PRINTS_THE_README_EXAMPLE = "demo が印字する README の書き方の例(注記なし)"

#: doeff の公開の名のうち、型が分からないままの物と訳。
_DOEFF_GAPS: dict[str, str] = {
    "doeff.cli.demo.digest": _DEMO_PRINTS_THE_README_EXAMPLE,
    "doeff.cli.demo.canned": _DEMO_PRINTS_THE_README_EXAMPLE,
    "doeff.cli.demo.trace": _DEMO_PRINTS_THE_README_EXAMPLE,
    "doeff.cli.demo.budget": _DEMO_PRINTS_THE_README_EXAMPLE,
    "doeff.cli.demo.live_world": _DEMO_PRINTS_THE_README_EXAMPLE,
}

#: doeff_core_effects の頭の名のうち、型が分からないままの物と訳(今は無い — 足す時は訳と一緒に書く)。
_CORE_HEAD_GAPS: dict[str, str] = {}
#: doeff_core_effects の module のうち、公開の名の全部に型が分かる物(この検が守る範囲)。今は全部を下の隠した module で数える。
_CORE_TYPED_MODULES: tuple[str, ...] = ()
#: 守る範囲のうち、名に _ を付けて隠した module(ADR-DOE-API-001 R5・card ki-c09b3b527fad・cache と memo は card ki-375e0a35b7a0)。
#: verifytypes は _ の module の名を数えないので、入口がこれらの module から出す名
#: (``doeff_core_effects.Task`` とその欄)で数える。
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
#: 廃止した名の置き物(実行時だけに在り、使うと替わりの名を言う)。
_REMOVED = {
    "Delegate": "use 'yield effect' to re-perform in handler body",
    "KleisliProgram": "use @do instead",
    "default_handlers": "compose handlers by calling handler(program)",
    "async_run": "use run() with scheduled()",
}


def _verifytypes(directory: Path, package: str) -> list[dict]:
    """``pyright --verifytypes <package>`` の公開の名の行。repo の pyrightconfig.json(extraPaths で package を手元の
    source として解く — verifytypes は入れた package を見つけられない)の外の dir から、開発の環境の python を指して走らせる。

    verifytypes は検索 path を問う python を ``--pythonpath`` でなく PATH の ``python`` から `-I` で起こす(1.1.411 で実測 —
    包みを ``--pythonpath`` にだけ渡すと、site の起動の .pyc を checkout へ書いた)。だから `-B` の包みを PATH の頭にも置く。"""
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
    assert completeness["packageRootDirectory"], f"verifytypes が {package} を見つけていない: {report}"
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
    assert unlisted == [], f"型の分からない公開の名 — 注記を付ける: {unlisted}"


def test_each_listed_gap_of_doeff_is_still_a_gap(doeff_symbols: list[dict]) -> None:
    closed = sorted(set(_DOEFF_GAPS) - _unknown(doeff_symbols))
    assert closed == [], f"型が付いた — 表から外して守る範囲に入れる: {closed}"


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


def _entry_names_from_hidden_modules() -> set[str]:
    """入口が ``from <隠した module> import X as X`` で出す名。

    即座に結ぶ名と ``if TYPE_CHECKING:`` の名の両方。
    """
    tree = ast.parse(_CORE_ENTRY.read_text(encoding="utf-8"))
    return {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and _in_module(node.module or "", _CORE_TYPED_HIDDEN_MODULES)
        for alias in node.names
    }


def test_every_public_name_of_the_typed_core_modules_has_a_known_type(core_symbols: list[dict]) -> None:
    entry_names = tuple(f"doeff_core_effects.{name}" for name in _entry_names_from_hidden_modules())
    covered = [
        symbol for symbol in core_symbols
        if _in_module(symbol["name"], (*_CORE_TYPED_MODULES, *entry_names))
    ]
    assert len(covered) > 100, len(covered)
    assert sorted(_unknown(covered)) == []


def test_every_head_name_of_doeff_core_effects_has_a_known_type_but_the_listed_gaps(
    core_symbols: list[dict],
) -> None:
    head = [symbol for symbol in core_symbols if symbol["name"].count(".") == 1]
    unknown = _unknown(head)
    assert sorted(unknown - set(_CORE_HEAD_GAPS)) == [], "型の分からない頭の名 — 注記を付ける"
    assert sorted(set(_CORE_HEAD_GAPS) - unknown) == [], "型が付いた — 表から外して守る範囲に入れる"


def test_type_checkers_do_not_see_the_removed_names(doeff_symbols: list[dict]) -> None:
    exported = {symbol["name"] for symbol in doeff_symbols}
    assert sorted(f"doeff.{name}" for name in _REMOVED if f"doeff.{name}" in exported) == []


@pytest.mark.parametrize(("name", "replacement"), sorted(_REMOVED.items()))
def test_a_removed_name_still_imports_and_names_its_replacement_on_use(name: str, replacement: str) -> None:
    import doeff

    removed = getattr(doeff, name)
    with pytest.raises(RuntimeError, match=f"^{re.escape(f'{name} was removed: {replacement}')}$"):
        removed()
