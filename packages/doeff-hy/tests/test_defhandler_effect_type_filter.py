"""A test that the effect types of defhandler clauses are passed to the VM's type filter.

defhandler turns the clause's effect type into the type annotation of the effect argument of the `(fn [effect k] ...)` it generates. The VM reads it once at install time
(doeff_vm._effect_types, SPEC-WITHHANDLER-TYPE-FILTER) and, for an effect outside the type, skips this handler without entering Python — effects outside the clauses
were already passed outward with `(Pass effect k)`, so the answers and order do not change; only the number of times the handler body is entered decreases.
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
    """The code of the handler body the VM calls (`(fn [effect k] ...)`), from a defhandler value."""
    data = handler_fn.__doeff_handler_data__  # type: ignore[attr-defined] — a field a defhandler value has (handle.hy)
    return data.__wrapped__.__code__


def _entries(codes: tuple[CodeType, ...], run: Callable[[], object]) -> tuple[tuple[int, ...], object]:
    """The number of times each function in codes started during run (in the order of codes), and the answer of run. The body is a generator, so a call
    event also occurs at each resume — count by frame identity (keep the frames alive to prevent id reuse)."""
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
    assert entered == 1, "Pong reaches the outer pongs without entering the body of pings (only Ping enters once)"


def test_a_handler_without_declared_types_still_sees_every_effect() -> None:
    # A handler without a type annotation (a plain Python function, or a defhandler whose clause is EffectBase) receives all effects as before.
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
    from doeff import Pass, do, handler, with_handlers

    @do
    def plain(effect, k):  # a plain handler without a type annotation
        yield Pass(effect, k)

    # The last in the list is the innermost: effects arrive in the order plain → everything → pings → pongs
    # with_handlers takes only handlers (ADR-DOE-CORE-003): a defhandler value, and an @do function passed through as a handler
    stack = [ns[name] for name in ("pongs", "pings", "everything")] + [handler(plain)]
    codes = (_body_code(ns["everything"]), plain.__wrapped__.__code__)
    (everything, bare), result = _entries(codes, lambda: ns["doeff_run"](with_handlers(stack, ns["body"])))
    assert result == "a:pongb:ping"
    assert everything == 2, "a defhandler whose clause is EffectBase receives all effects"
    assert bare == 2, "a plain handler without a type annotation receives all effects"


def test_answers_and_order_are_the_same_as_passing_through() -> None:
    # A Ping rejected at the :when turn is passed outward — even when turn rejection and type-based skipping mix, the answering handler and the order of answers do not change
    # (the answering handler's name is carried in the answer value).
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
    assert inner == 2, "inner's body is entered only for 2 Pings (Pong is skipped; a Ping rejected at its turn is entered and then passed outward)"
    assert outer == 2, "outer answers the Pong and Ping that inner passed on"


@pytest.mark.skipif(sys.version_info >= (3, 14), reason="from 3.14 type annotations are evaluated lazily, so write the real annotation")
def test_before_3_14_the_types_are_written_as_text() -> None:
    ns = _eval("(defhandler pings (Ping [value] (resume value)))")
    # The annotated argument is the handler function's effect argument `_doeff_effect` (a name kept apart from the author's name effect — effect-symbol in handle.hy).
    annotation = ns["pings"].__doeff_handler_data__.__wrapped__.__annotations__["_doeff_effect"]  # type: ignore[attr-defined]
    assert annotation == "Ping"
