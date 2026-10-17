"""公開の名の型が型の道具に分かる事の検(型の報告の抜け 6・card ki-23e84023ab9c)。

pyright の verifytypes は、package の公開の名ごとに、引数と戻りと属性の型がすべて分かるかを数える。核の 4 個だけの環境で
測ると、doeff は 78%・doeff_core_effects は 87.3% だった(2026-10-17・main 7267f0105)。分からない名を使う人の strict の
型の check は Unknown の error を出す。訳は 3 つだった:

- scheduler の内の名(``Task``・``Future``・``Promise``・``Semaphore`` の id、``ExternalPromise.fail``、
  ``SchedulerDeadlockError`` の欄)と effect の ``__repr__`` に注記が無く、class ごと「一部分からない」になっていた。
  ``Tell`` と ``slog`` は戻りの class(``WriterTellEffect``・``SlogEffect``)を通して分からなかった。
- 頭の handler(``writer`` など)が注記の無い変数で、道具によって推す型が違いうる(ambiguous)。
- 廃止した名の置き物(``KleisliProgram`` など 17 個)が、型の無い ``_Removed`` の値として見えていた。置き物は型の道具から
  隠した: 使えば必ず落ちる名を型の check が通すより、import の行で error にする方が早く知らせる(doeff に無い名と同じ —
  tests/test_unknown_doeff_import_static_types.py)。実行時の import と案内の文は変わらない。

直した後は doeff 95.4%・doeff_core_effects 92.2%(核の 4 個だけの環境・pyright 1.1.414)。

ここでは開発の環境で verifytypes を走らせ、型の分からない公開の名が、下の表に訳を書いた物だけである事を確かめる。表の名に
型が付いた時(別の card が直した時)も赤にする — 表から外して、守る範囲を広げる。

失敗ケース: 公開の module に注記の無い関数・属性・変数を足す・``Task`` の id の注記を外す・置き物を型の道具に見せる、で赤。
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from doeff_hy.static_check import python_without_bytecode

#: doeff の公開の名のうち、型が分からないままの物と訳。
_DOEFF_GAPS: dict[str, str] = {
    # Some・Maybe・Nothing に型が無い — 型の報告の抜け 5(doeff.result の card)が型を付ける。
    "doeff.Some": "doeff.result",
    "doeff.Maybe": "doeff.result",
    "doeff.Nothing": "doeff.result",
    # 型引数を書かない program の node の union(isinstance に渡せる形)。node の型引数に既定値 Any を持たせるのは
    # doeff_vm の stub の側で、変えると doeff-vm の Rust の組み立て直しが起きる(tool.uv.cache-keys が .pyi を含む)。
    "doeff.DoExpr": "program の node の型引数",
    "doeff.ProgramBase": "program の node の型引数",
}
#: 上と同じ訳で型が分からない module(この接頭の名は全部認める)。
_DOEFF_GAP_MODULES: dict[str, str] = {"doeff.result.": "doeff.result"}

#: doeff_core_effects の頭の名のうち、型が分からないままの物と訳(outcomes の境目の handler — 答えの Maybe と、
#: 束ねる物の union Bindable の DoExpr に依る)。
_CORE_HEAD_GAPS: dict[str, str] = {
    "doeff_core_effects.RaiseCase": "doeff.result",
    "doeff_core_effects.absent_as": "program の node の型引数",
    "doeff_core_effects.maybe": "doeff.result",
    "doeff_core_effects.on_raise": "doeff.result",
    "doeff_core_effects.result": "doeff.result",
}
#: doeff_core_effects の module のうち、公開の名の全部に型が分かる物(この検が守る範囲)。
_CORE_TYPED_MODULES = (
    "doeff_core_effects.effects",
    "doeff_core_effects.scheduler",
    "doeff_core_effects.handlers",
    "doeff_core_effects.cache_effects",
    "doeff_core_effects.cache_handlers",
    "doeff_core_effects.memo_effects",
    "doeff_core_effects.memo_handlers",
    "doeff_core_effects.storage",
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
    result = subprocess.run(
        [sys.executable, "-m", "pyright", "--project", str(directory / "pyrightconfig.json"),
         "--pythonpath", str(python), "--verifytypes", package, "--outputjson"],
        cwd=directory, capture_output=True, text=True, timeout=180, check=False,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
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


def _is_doeff_gap(name: str) -> bool:
    return name in _DOEFF_GAPS or name.startswith(tuple(_DOEFF_GAP_MODULES))


def test_every_public_name_of_doeff_has_a_known_type_but_the_listed_gaps(doeff_symbols: list[dict]) -> None:
    unknown = _unknown(doeff_symbols)
    unlisted = sorted(name for name in unknown if not _is_doeff_gap(name))
    assert unlisted == [], f"型の分からない公開の名 — 注記を付ける: {unlisted}"


def test_each_listed_gap_of_doeff_is_still_a_gap(doeff_symbols: list[dict]) -> None:
    unknown = _unknown(doeff_symbols)
    closed = sorted(name for name in _DOEFF_GAPS if name not in unknown)
    closed += sorted(
        prefix for prefix in _DOEFF_GAP_MODULES if not any(name.startswith(prefix) for name in unknown)
    )
    assert closed == [], f"型が付いた — 表から外して守る範囲に入れる: {closed}"


def test_the_scheduler_handles_and_tell_and_slog_have_known_types(core_symbols: list[dict]) -> None:
    known = {symbol["name"] for symbol in core_symbols if symbol["isTypeKnown"]}
    named = {
        f"doeff_core_effects.{name}"
        for name in ("Task", "Future", "Promise", "Semaphore", "ExternalPromise", "SchedulerDeadlockError",
                     "Gather", "Race", "Wait", "Cancel", "Tell", "slog", "WriterTellEffect", "SlogEffect", "writer")
    }
    assert sorted(named - known) == []


def test_every_public_name_of_the_typed_core_modules_has_a_known_type(core_symbols: list[dict]) -> None:
    covered = [
        symbol for symbol in core_symbols
        if any(symbol["name"] == module or symbol["name"].startswith(f"{module}.") for module in _CORE_TYPED_MODULES)
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
