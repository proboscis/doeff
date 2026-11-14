"""手入れの effect の組 MAINTENANCE_EFFECTS が、手入れの effect の全部で、記録の handler の組が全部に答える事の検。

使い手の閉じの検は、実行時に選ぶ手入れの係の handler(解析器が読めない)が答える effect をこの組から組む。組が手入れの effect の
全部でないと、手入れの effect を足した時に使い手の閉じの検が赤になる(MoveBulkFields を足した時に起きた)。

- 組 = _maintenance の effect の全部。失敗ケース: _maintenance に effect を足して組に足さないと赤。
- memory と PostgreSQL の記録の handler は、組のどれにも答える(解析器が節から読む)。失敗ケース: handler が組の effect の節を
  持たないと赤。
"""

import importlib.util
import inspect
import sys
from pathlib import Path
from typing import TypeVar

import hy  # noqa: F401  Hy の module を読むため
import pytest
from doeff_records import MAINTENANCE_EFFECTS
from doeff_records import _maintenance as maintenance

from doeff import EffectBase, run

# doeff-effect-analyzer の Python の front end(開発の道具 — doeff-records の実行時の依存ではない。test_event_source_closure.py と
# 同じ扱い)。
if importlib.util.find_spec("doeff_effect_analyzer") is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "doeff-effect-analyzer" / "python"))

from doeff_effect_analyzer import AnalyzeHandler, Basis, effect_analysis

_Answer = TypeVar("_Answer")


def _answered(effect: EffectBase[_Answer]) -> _Answer:
    """Read one analysis effect under the analyzer's handler (the analyzer reads the sources; nothing is run)."""
    return run(effect_analysis()(effect))


def test_the_maintenance_effects_are_every_effect_of_the_maintenance_module() -> None:
    defined = {
        value
        for value in vars(maintenance).values()
        if inspect.isclass(value)
        and issubclass(value, EffectBase)
        and value.__module__ == maintenance.__name__
    }
    assert set(MAINTENANCE_EFFECTS) == defined
    assert len(MAINTENANCE_EFFECTS) == len(defined)


@pytest.mark.parametrize(
    "handler",
    ["doeff_records._memory:memory_records_handler", "doeff_records._pg:pg_records_handler"],
)
def test_the_records_handlers_answer_every_maintenance_effect(handler: str) -> None:
    read = _answered(AnalyzeHandler(handler))
    assert read.basis is Basis.CLAUSES, read.unresolved
    assert set(MAINTENANCE_EFFECTS) - read.handled == set()
