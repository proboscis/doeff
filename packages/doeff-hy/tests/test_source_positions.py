"""doeff-hy の macro の中で落ちた時、traceback が落ちた行を指すこと。

macro が作り直した式は位置を持たず、Hy が macro 呼び出し(`defk` の頭)の位置で埋めていた。
`doeff_hy.positions.locate-synthesized` が合成した式に、包んでいる利用者の式の範囲を付ける。
Hy の source を一時 file に書いて import し、落ちた行を traceback の枠の行番号で確かめる
(`hy.eval` では filename と行が source に対応しないため)。
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

# 1 始まりの行番号(SOURCE の中の位置)。
LINE_DIVIDE = 17  # (setv c (// x 0))
LINE_WRITER_BAD = 25  # (<- again bool (Write ...))
LINE_HANDLER_RAISE = 11  # (raise (RuntimeError ...))
LINE_BANG_BAD = 31  # (and first (! (Write "bad")))
LINE_DO_BAD = 35  # (<- b (Write "bad"))


@pytest.fixture(scope="module")
def mod(tmp_path_factory: pytest.TempPathFactory) -> ModuleType:
    import doeff_hy  # noqa: F401 - Hy の import hook を登録する

    root: Path = tmp_path_factory.mktemp("doeff_hy_positions")
    (root / "positions_probe.hy").write_text(SOURCE, encoding="utf-8")
    sys.path.insert(0, str(root))
    sys.dont_write_bytecode = True
    try:
        return importlib.import_module("positions_probe")
    finally:
        sys.path.remove(str(root))
        sys.dont_write_bytecode = False


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


