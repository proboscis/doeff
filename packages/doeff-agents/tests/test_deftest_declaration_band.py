"""deftest に書いたマークが、pytest へ公開する経路を通しても届く。

根(2026-09-21): shim の ``_make_wrapper`` が deftest を呼ぶだけの新しい関数を
作り、``__name__`` と ``__doc__`` だけをコピーしていた。pytest のマークは関数の
``__dict__`` の ``pytestmark`` に載るので、包み直した時点で落ちる。書いてある・
macro も付けている・しかし pytest に届かない ⇒ 書いた人は効いていると思い込む
(実測: ``:skip-if`` の 12 件が 1 つも効かず、tmux の無い機械で赤になっていた)。

字面(``_make_wrapper`` の有無)ではなく**マークの集合の一致**を見るので、別の
包み方が入っても捕まえる。deftest を公開している ``.py`` は自動で見つけるので、
新しい公開 file が増えても自動で対象に入る。
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


def _deftest_sources(module: ModuleType) -> list[ModuleType]:
    """module が参照している deftest モジュール(名前が ``deftests`` で終わるもの)。"""
    return [
        value
        for name, value in vars(module).items()
        if name.endswith("deftests") and isinstance(value, ModuleType)
    ]


def _exposing_modules() -> list[tuple[str, ModuleType]]:
    """deftest を pytest へ公開している ``.py``。"""
    found = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        module = importlib.import_module(path.stem)
        if _deftest_sources(module):
            found.append((path.name, module))
    return found


def _marks(obj: object) -> list[tuple]:
    if not hasattr(obj, "pytestmark"):
        return []
    return [
        (mark.name, mark.args, tuple(sorted(mark.kwargs.items())))
        for mark in obj.pytestmark
    ]


_EXPOSING = _exposing_modules()


def test_exposing_modules_are_discovered() -> None:
    """公開している .py が 1 つも見つからない = この検査が空振りしている。"""
    assert _EXPOSING, "deftest を公開する .py が 1 つも見つからない — 検査が空振りしている"


@pytest.mark.parametrize(
    "module_name,module", _EXPOSING, ids=[name for name, _ in _EXPOSING]
)
def test_declaration_band_survives_exposure(
    module_name: str, module: ModuleType
) -> None:
    """公開された関数は、元の deftest と同じマークを持つ(skipif / marks / parametrize)。"""
    origins: dict[str, object] = {}
    for source in _deftest_sources(module):
        for name, value in vars(source).items():
            if name.startswith("test_") and callable(value):
                origins[name] = value

    exposed = {
        name: value
        for name, value in vars(module).items()
        if name.startswith("test_") and callable(value) and name in origins
    }
    assert exposed, f"{module_name}: deftest を 1 つも公開していない"

    for name, exposed_fn in sorted(exposed.items()):
        origin = origins[name]
        assert _marks(exposed_fn) == _marks(origin), (
            f"{module_name}::{name} — deftest に書いたマークが公開時に落ちている。"
            f" 元={_marks(origin)} 公開後={_marks(exposed_fn)}。"
            " deftest は包み直さずそのまま公開すること"
            "(ADR-DOE-HY-002 params_silently_dropped == 0)"
        )
