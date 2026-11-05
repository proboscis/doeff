"""Check that the VM skips a fence-shaped defhandler (a catch-all clause whose guard is "unless X") for effects of type X.

A handler whose clause is an EffectBase catch-all with the guard `(not (isinstance effect X))` only passes effects of type X, which no earlier clause names, outward with
`(Pass effect k)`. A type annotation cannot say "everything except X", so the VM used to enter this handler for every effect.
When defhandler finds this shape, it hands X and the earlier clauses' types to the VM at install time (doeff_vm._effect_types.declare_passes), and the VM
skips this handler for such effects without entering Python — it ends the same way as when the guard fails, so the answers and the order are unchanged.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from types import CodeType, FrameType

import pytest

from doeff._vm import PassedEffects, handler_spec

PRELUDE = """
(require doeff-hy.macros [defp defhandler with-handler <-])
(import doeff [do :as _doeff-do EffectBase run :as doeff-run])
(import dataclasses [dataclass])

(defclass [(dataclass :frozen True)] Ping [EffectBase]
  #^ str value)

(defclass [(dataclass :frozen True)] Pong [EffectBase]
  #^ str value)

(defclass [(dataclass :frozen True)] Zap [EffectBase]
  #^ str value)

(defhandler pongs (Pong [value] (resume (+ value ":pong"))))
(defhandler pings (Ping [value] (resume (+ value ":outer"))))

;; Fence shape: the earlier clause is Ping; the catch-all rejects anything outside passable
(defhandler fence [passable]
  (Ping [value] (resume (+ value ":fence")))
  (EffectBase []
    :when (not (isinstance effect passable))
    (raise (ValueError (+ "fenced " (. (type effect) __name__))))))
"""


def _eval(code: str) -> dict[str, object]:
    import doeff_hy  # noqa: F401 - register Hy import hooks
    import hy

    namespace: dict[str, object] = {}
    hy.eval(hy.read_many(PRELUDE + code), namespace)
    return namespace


def _data(handler_fn: object) -> object:
    """The handler function (`__doeff_handler_data__`) that the VM installs, taken from the defhandler value."""
    return handler_fn.__doeff_handler_data__  # type: ignore[attr-defined] — a field held by the defhandler value (handle.hy)


def _entries(code: CodeType, run: Callable[[], object]) -> tuple[int, object]:
    """The number of times the code function started during the run, and the run's answer. The body is a generator, so a call event also fires on each resume — counted by frame identity."""
    frames: dict[int, FrameType] = {}

    def profile(frame: FrameType, event: str, _arg: object) -> None:
        if event == "call" and frame.f_code is code:
            frames.setdefault(id(frame), frame)

    sys.setprofile(profile)
    try:
        result = run()
    finally:
        sys.setprofile(None)
    return len(frames), result


def test_a_passed_effect_does_not_enter_the_fence_and_an_earlier_clause_type_still_does() -> None:
    ns = _eval("""
(defp body {:post [(: % tuple)]}
  (<- a (Pong :value "a"))
  (<- b (Pong :value "b"))
  (<- c (Pong :value "c"))
  (<- d (Ping :value "d"))
  #(a b c d))
(setv guarded (fence #(Ping Pong)))
(setv program (with-handler [pongs guarded] body))
""")
    code = _data(ns["guarded"]).__wrapped__.__code__
    entered, result = _entries(code, lambda: ns["doeff_run"](ns["program"]))
    assert result == ("a:pong", "b:pong", "c:pong", "d:fence")
    assert entered == 1, "3 Pongs do not enter the fence but reach the outer pongs; the fence answers Ping, the earlier clause's type"


def test_an_effect_outside_the_passed_types_still_enters_and_is_refused() -> None:
    ns = _eval("""
(defp body {:post [(: % str)]}
  (<- a (Pong :value "a"))
  (<- z (Zap :value "z"))
  (+ a z))
(setv program (with-handler [pongs (fence #(Pong))] body))
""")
    with pytest.raises(ValueError, match="fenced Zap"):
        ns["doeff_run"](ns["program"])
    assert handler_spec(_data(ns["fence"]((ns["Pong"],)))).passed == PassedEffects((ns["Pong"],), (ns["Ping"],))


def test_the_passed_types_resolve_at_install_and_other_guard_shapes_are_not_skipped() -> None:
    ns = _eval("""
;; A constant for the guard type, written after the handler (resolved at install time)
(defhandler gate
  (EffectBase []
    :when (not (isinstance effect LATER))
    (raise (ValueError "gated"))))
;; The guard has a different shape — not skipped (entered for every effect)
(defhandler custom
  (EffectBase []
    :when (not (= (. (type effect) __name__) "Pong"))
    (raise (ValueError "custom"))))
(setv LATER Pong)
""")
    assert handler_spec(_data(ns["gate"])).passed == PassedEffects((ns["Pong"],), ())
    assert handler_spec(_data(ns["custom"])).passed is None
