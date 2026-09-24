"""deftest に書いたマークが、pytest へ公開する経路を通しても届く(構造の検)。

根(2026-09-21): 公開 file の包み直しの関数が deftest を呼ぶだけの新しい
関数を作り、``__name__`` と ``__doc__`` だけをコピーしていた。pytest のマークは
関数の ``__dict__`` の ``pytestmark`` に載るので、包み直した時点で落ちる。
書いてある・macro も付けている・しかし pytest に届かない ⇒ 書いた人は効いて
いると思い込む(実測: ``:skip-if`` の 12 件が 1 つも効かず、tmux の無い機械で
赤になっていた)。

字面(包む関数の有無)ではなく**マークの到達**を見るので、別の包み方が
入っても捕まえる。deftest の module(名前が ``deftests`` で終わる)を参照する
``test_*.py`` を自動で見つけるので、新しい公開 file が増えても自動で対象に入る。

判定: deftest に書いたマークは、同じ名前で公開された関数のマークに**全部含まれ**て
いなければならない(公開側が e2e / timeout 等を**足す**のは構わない — 落とすのが
違反)。マークを持つ deftest が同じ名前で公開されていないのも、そのマークが
pytest に届く経路が無いので違反。
ADR-DOE-HY-002 law deftest-params-are-honored(``params_silently_dropped == 0``)。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

import hy  # noqa: F401 -- installs the .hy import hook
import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

MarkShape = tuple[str, tuple, tuple]


def _deftest_sources(module: ModuleType) -> list[ModuleType]:
    """module が参照している deftest モジュール(名前が ``deftests`` で終わるもの)。"""
    return [
        value
        for name, value in vars(module).items()
        if name.endswith("deftests") and isinstance(value, ModuleType)
    ]


def _exposing_modules() -> list[tuple[str, ModuleType]]:
    """deftest を pytest へ公開している ``test_*.py``。"""
    found = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        module = importlib.import_module(path.stem)
        if _deftest_sources(module):
            found.append((path.name, module))
    return found


def _marks(obj: object) -> list[MarkShape]:
    """関数に載っている pytest のマーク(``__dict__`` の ``pytestmark``)を比較できる形で。"""
    marks = vars(obj).get("pytestmark") if hasattr(obj, "__dict__") else None
    if not marks:
        return []
    return [(mark.name, mark.args, tuple(sorted(mark.kwargs.items()))) for mark in marks]


def _origins(module: ModuleType) -> dict[str, object]:
    """module が参照する deftest モジュールの ``test_*`` deftest(名前 → 関数)。"""
    origins: dict[str, object] = {}
    for source in _deftest_sources(module):
        for name, value in vars(source).items():
            if name.startswith("test_") and callable(value):
                origins[name] = value
    return origins


_EXPOSING = _exposing_modules()


def test_exposing_modules_are_discovered() -> None:
    """公開 file が 1 つも無い / マークを書いた deftest が 1 つも無い = この検査が空振りしている。"""
    assert _EXPOSING, "deftest を公開する test_*.py が 1 つも見つからない — 検査が空振りしている"
    marked = [
        f"{module_name}::{name}"
        for module_name, module in _EXPOSING
        for name, origin in _origins(module).items()
        if _marks(origin)
    ]
    assert marked, "マークを書いた deftest が 1 つも無い — 検査が何も検めていない"


@pytest.mark.parametrize(("module_name", "module"), _EXPOSING, ids=[name for name, _ in _EXPOSING])
def test_declaration_band_survives_exposure(module_name: str, module: ModuleType) -> None:
    """deftest に書いたマーク(skipif / marks / parametrize)は、公開された同名の関数に全部載っている。"""
    origins = _origins(module)
    assert origins, f"{module_name}: 参照している deftest モジュールに test_* deftest が無い"

    dropped: list[str] = []
    for name, origin in sorted(origins.items()):
        declared = _marks(origin)
        if not declared:
            continue
        exposed_fn = vars(module).get(name)
        if exposed_fn is None or not callable(exposed_fn):
            dropped.append(
                f"{module_name}::{name} — マーク {declared} を書いた deftest が同じ名前で"
                " pytest に公開されていない(マークの届く経路が無い)"
            )
            continue
        reached = _marks(exposed_fn)
        missing = [mark for mark in declared if mark not in reached]
        if missing:
            dropped.append(
                f"{module_name}::{name} — deftest に書いたマークが公開時に落ちている。"
                f" 落ちた={missing} 元={declared} 公開後={reached}"
            )
    assert not dropped, (
        "\n".join(dropped)
        + "\n deftest は包み直さずそのまま公開すること(ADR-DOE-HY-002 params_silently_dropped == 0)"
    )
