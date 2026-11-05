"""When a crash happens inside a doeff-hy macro, the traceback points at the line that crashed.

Expressions a macro rebuilt had no position, and Hy filled them with the position of the macro call (the head of `defk`).
`doeff_hy._positions.locate-synthesized` attaches the range of the enclosing user expression to the synthesized expression.
Write the Hy source to a temporary file and import it, and check the crashed line by the line number of the traceback frame
(because with `hy.eval` the filename and lines do not correspond to the source).
"""

from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path
from types import ModuleType

import pytest

SOURCE = """\
(require doeff-hy.macros [defk deff <- do! defp defhandler])
(import dataclasses [dataclass])
(import doeff [EffectBase])

(defclass [(dataclass :frozen True)] Write [EffectBase]
  #^ str key)

(defhandler refusing
  (Write [key]
    (if (= key "bad")
        (raise (RuntimeError (+ "refused " key)))
        (resume True))))

(defk divide [x]
  {:pre [(: x int)] :post [(: % int)]}
  (setv a 1)
  (setv c (// x 0))
  c)

(defk writer [n]
  {:pre [(: n int)] :post [(: % int)]}
  (<- ok bool (Write "good"))
  (for [i (range n)]
    (when ok
      (<- again bool (Write (if (= i 1) "bad" "good")))))
  n)

(defk banged [n]
  {:pre [(: n int)] :post [(: % bool)]}
  (setv first (! (Write "good")))
  (and first (! (Write "bad"))))

(setv in-do (do!
  (<- a (Write "good"))
  (<- b (Write "bad"))
  b))
"""

# 1-based line number (position within SOURCE).
LINE_DIVIDE = 17  # (setv c (// x 0))
LINE_WRITER_BAD = 25  # (<- again bool (Write ...))
LINE_HANDLER_RAISE = 11  # (raise (RuntimeError ...))
LINE_BANG_BAD = 31  # (and first (! (Write "bad")))
LINE_DO_BAD = 35  # (<- b (Write "bad"))


@pytest.fixture(scope="module")
def mod(tmp_path_factory: pytest.TempPathFactory) -> ModuleType:
    import doeff_hy  # noqa: F401 - register the Hy import hook

    root: Path = tmp_path_factory.mktemp("doeff_hy_positions")
    (root / "positions_probe.hy").write_text(SOURCE, encoding="utf-8")
    sys.path.insert(0, str(root))
    dont_write_before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        return importlib.import_module("positions_probe")
    finally:
        sys.path.remove(str(root))
        sys.dont_write_bytecode = dont_write_before


def _hy_frames(error: BaseException, mod: ModuleType) -> list[tuple[str, int]]:
    return [
        (frame.name, frame.lineno or 0)
        for frame in traceback.extract_tb(error.__traceback__)
        if frame.filename == mod.__file__
    ]


def _run(program: object) -> object:
    from doeff import run

    return run(program)


def test_defk_body_error_points_at_the_failing_line(mod: ModuleType) -> None:
    with pytest.raises(ZeroDivisionError) as caught:
        _run(mod.divide(3))
    assert ("divide", LINE_DIVIDE) in _hy_frames(caught.value, mod)


def test_error_thrown_into_a_bind_points_at_the_bind_and_the_handler_raise(
    mod: ModuleType,
) -> None:
    with pytest.raises(RuntimeError, match="refused bad") as caught:
        _run(mod.refusing(mod.writer(3)))
    frames = _hy_frames(caught.value, mod)
    assert ("writer", LINE_WRITER_BAD) in frames
    assert LINE_HANDLER_RAISE in [line for _, line in frames]


def test_bang_rewrite_keeps_the_line(mod: ModuleType) -> None:
    with pytest.raises(RuntimeError, match="refused bad") as caught:
        _run(mod.refusing(mod.banged(1)))
    assert ("banged", LINE_BANG_BAD) in _hy_frames(caught.value, mod)


def test_do_block_bind_points_at_the_failing_bind(mod: ModuleType) -> None:
    with pytest.raises(RuntimeError, match="refused bad") as caught:
        _run(mod.refusing(mod.in_do))
    assert LINE_DO_BAD in [line for _, line in _hy_frames(caught.value, mod)]




# The smallest Hy where line numbers drift. Every binding in a defk body looks up the outcomes module by the same name.
# If that name is reused as a single Symbol across expansions, the position locate-synthesized attached in the first expansion stays on
# that Symbol, and the start of the (! …) expression in later expansions (including the yield into which failures are thrown) is pulled to that line.
# The remaining position is the line of "the binding expanded first in that process", so in a traceback the check turns red or green depending on order.
# So look at the result of the expansion directly: two expansions do not share a model, and the yield has the line of the (! …) it wrote.
FIRST_BANG = "(defk first-bang [] {:pre [] :post [(: % int)]} (! (Pure 1)))"
SECOND_BANG = """\
(defk second-bang []
  {:pre [] :post [(: % int)]}
  (! (Pure 2)))
"""
LINE_SECOND_BANG_IN_SOURCE = 3  # (! (Pure 2))


def _expand_defk(source: str) -> object:
    import hy

    import doeff_hy  # noqa: F401 - register the Hy import hook

    module = ModuleType("doeff_hy_bang_expansion_probe")
    hy.eval(hy.read("(require doeff-hy.macros [defk])"), module=module)
    return hy.macroexpand(hy.read(source), module)


def _models(tree: object) -> list[object]:
    from hy.models import Sequence

    children = [model for child in tree for model in _models(child)] if isinstance(tree, Sequence) else []
    return [tree, *children]


def _yields(tree: object) -> list[object]:
    from hy.models import Expression, Symbol

    return [
        model
        for model in _models(tree)
        if isinstance(model, Expression) and len(model) > 0 and model[0] == Symbol("yield")
    ]


def test_bang_expansions_share_no_model_and_keep_the_written_line() -> None:
    from hy.models import Object

    first = _expand_defk(FIRST_BANG)
    second = _expand_defk(SECOND_BANG)
    first_ids = {id(model) for model in _models(first) if isinstance(model, Object)}
    shared = [str(model) for model in _models(second) if isinstance(model, Object) and id(model) in first_ids]
    assert shared == []
    lines = [getattr(model, "start_line", None) for model in _yields(second)]
    assert lines != []
    assert set(lines) == {LINE_SECOND_BANG_IN_SOURCE}
