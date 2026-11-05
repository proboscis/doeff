"""The expansion of defk / deff / defp reads the extension of the file being converted from the filename of Hy's compiler, and does not walk inspect.stack().

Origin: at every expansion inspect.stack() read the source of every level, and the first conversion of a large .hy file took minutes
(collecting controllers/screen/tests/test_server.hy in agora-controllers took 343 seconds without cache -> 271 seconds).
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
    # Hy itself (resolving the calling module of hy.eval, require) also calls inspect.stack(), so only calls from doeff-hy code are counted.
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
