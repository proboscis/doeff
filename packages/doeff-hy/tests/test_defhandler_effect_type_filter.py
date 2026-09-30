"""defhandler の節の effect の型を、VM の型の選り分けに渡す事の検(agora-redesign #1931)。

defhandler は節の effect の型を、生成する `(fn [effect k] ...)` の effect の引数の型の註にする。VM はそれを install の時に 1 度読み
(doeff_vm._effect_types・SPEC-WITHHANDLER-TYPE-FILTER)、型の外の effect ではこの handler を Python に入らずに飛ばす — 節の外の
effect は今までも `(Pass effect k)` で外へ渡していたので、答えと順は変わらず、handler の本体に入る回数だけが減る。
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from types import CodeType, FrameType

import pytest

PRELUDE = """
(require doeff-hy.macros [defp defhandler with-handler <-])
(import doeff [do :as _doeff-do EffectBase run :as doeff-run])
(import dataclasses [dataclass])

(defclass [(dataclass :frozen True)] Ping [EffectBase]
  #^ str value)

(defclass [(dataclass :frozen True)] Pong [EffectBase]
  #^ str value)
"""


def _eval(code: str) -> dict[str, object]:
    import doeff_hy  # noqa: F401 - register Hy import hooks
    import hy

    namespace: dict[str, object] = {}
    hy.eval(hy.read_many(PRELUDE + code), namespace)
    return namespace


def _body_code(handler_fn: object) -> CodeType:
    """defhandler の値から、VM が呼ぶ handler の本体(`(fn [effect k] ...)`)の code。"""
    data = handler_fn.__doeff_handler_data__  # type: ignore[attr-defined] — defhandler の値が持つ欄(handle.hy)
    return data.__wrapped__.__code__


def _entries(codes: tuple[CodeType, ...], run: Callable[[], object]) -> tuple[tuple[int, ...], object]:
    """run の間に codes の各関数が始まった回数(codes の順)と、run の答え。本体は generator なので、再開ごとにも call の
    事象が出る — frame の同一性で数える(frame を持ち続けて id の使い回しを防ぐ)。"""
    frames: dict[int, FrameType] = {}

    def profile(frame: FrameType, event: str, _arg: object) -> None:
        if event == "call" and frame.f_code in codes:
            frames.setdefault(id(frame), frame)

    sys.setprofile(profile)
    try:
        result = run()
    finally:
        sys.setprofile(None)
    return tuple(sum(1 for f in frames.values() if f.f_code is code) for code in codes), result


def test_an_effect_outside_the_clauses_types_does_not_enter_the_handler() -> None:
    ns = _eval("""
(defhandler pings (Ping [value] (resume (+ value ":ping"))))
(defhandler pongs (Pong [value] (resume (+ value ":pong"))))
(defp body {:post [(: % str)]}
  (<- a (Pong :value "a"))
  (<- b (Pong :value "b"))
  (<- c (Pong :value "c"))
  (<- d (Ping :value "d"))
  (+ a b c d))
(setv program (with-handler [pongs pings] body))
""")
    (entered,), result = _entries((_body_code(ns["pings"]),), lambda: ns["doeff_run"](ns["program"]))
    assert result == "a:pongb:pongc:pongd:ping"
    assert entered == 1, "Pong は pings の本体に入らずに外の pongs へ届く(Ping の 1 回だけ入る)"


def test_a_handler_without_declared_types_still_sees_every_effect() -> None:
    # 型の註の無い handler(Python の素の関数・節が EffectBase の defhandler)は今までどおり全部の effect を受ける。
    ns = _eval("""
(defhandler everything
  (EffectBase [] (reperform effect)))
(defhandler pongs (Pong [value] (resume (+ value ":pong"))))
(defhandler pings (Ping [value] (resume (+ value ":ping"))))
(defp body {:post [(: % str)]}
  (<- a (Pong :value "a"))
  (<- b (Ping :value "b"))
  (+ a b))
""")
    from doeff import Pass, do, with_handlers

    @do
    def plain(effect, k):  # 型の註の無い素の handler
        yield Pass(effect, k)

    # 列の最後が一番内側: plain → everything → pings → pongs の順に effect が届く
    stack = [ns[name].__doeff_handler_data__ for name in ("pongs", "pings", "everything")] + [plain]  # type: ignore[attr-defined] — defhandler の値が持つ欄(handle.hy)
    codes = (_body_code(ns["everything"]), plain.__wrapped__.__code__)
    (everything, bare), result = _entries(codes, lambda: ns["doeff_run"](with_handlers(stack, ns["body"])))
    assert result == "a:pongb:ping"
    assert everything == 2, "節が EffectBase の defhandler は全部の effect を受ける"
    assert bare == 2, "型の註の無い素の handler は全部の effect を受ける"


def test_answers_and_order_are_the_same_as_passing_through() -> None:
    # :when の番で外れた Ping は外へ渡る — 番の外れと型の外の飛ばしが混ざっても、答えた handler と答えの順は変わらない
    # (答えの値に答えた handler の名が載る)。
    ns = _eval("""
(defhandler outer
  (Ping [value] (resume (+ value ":outer")))
  (Pong [value] (resume (+ value ":outer"))))
(defhandler inner
  (Ping [value] :when (= value "mine") (resume (+ value ":inner"))))
(defp body {:post [(: % tuple)]}
  (<- a (Ping :value "mine"))
  (<- b (Pong :value "x"))
  (<- c (Ping :value "other"))
  #(a b c))
(setv program (with-handler [outer inner] body))
""")
    (inner, outer), result = _entries((_body_code(ns["inner"]), _body_code(ns["outer"])), lambda: ns["doeff_run"](ns["program"]))
    assert result == ("mine:inner", "x:outer", "other:outer")
    assert inner == 2, "inner の本体に入るのは Ping の 2 回だけ(Pong は飛ばす・番で外れた Ping は入ってから外へ渡す)"
    assert outer == 2, "outer は inner が渡した Pong と Ping に答える"


@pytest.mark.skipif(sys.version_info >= (3, 14), reason="3.14 からは型の註が遅れて評価されるので、実の註を書く")
def test_before_3_14_the_types_are_written_as_text() -> None:
    ns = _eval("(defhandler pings (Ping [value] (resume value)))")
    annotation = ns["pings"].__doeff_handler_data__.__wrapped__.__annotations__["effect"]  # type: ignore[attr-defined]
    assert annotation == "Ping"
