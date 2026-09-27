"""defk / deff / defp の展開は、変換中の file の拡張子を Hy の compiler の filename から読み、inspect.stack() を回さない。

出自 = agora-redesign #786: 展開のたびに inspect.stack() が全段の source を読み、大きな .hy file の最初の変換が数分かかっていた
(agora-controllers の controllers/screen/tests/test_server.hy の収集が cache なしで 343 秒 → 271 秒)。
"""

from __future__ import annotations

import inspect
import sys

import hy
import pytest


SOURCE = """
(require doeff-hy.macros [defk deff defp <-])
(defk plus-one [x] {:pre [(: x int)] :post [(: % int)]} (+ x 1))
(deff twice [x] {:pre [(: x int)] :post [(: % int)]} (* x 2))
(defp answer {:post [(: % int)]} 42)
"""


def test_expanding_defk_deff_defp_does_not_walk_the_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    # Hy 本体(hy.eval の呼び手の module の解決・require)も inspect.stack() を呼ぶので、数えるのは doeff-hy の code からの呼びだけ。
    original = inspect.stack
    callers: list[str] = []

    def counted(*args: object, **kwargs: object) -> list[inspect.FrameInfo]:
        caller = sys._getframe(1).f_code.co_filename
        if "doeff_hy" in caller:
            callers.append(caller)
        return original(*args, **kwargs)

    monkeypatch.setattr(inspect, "stack", counted)
    namespace: dict[str, object] = {"__name__": "macro_file_ext_probe"}
    hy.eval(hy.read_many(SOURCE), namespace)
    assert "plus_one" in namespace and "twice" in namespace and "answer" in namespace
    assert callers == [], callers
