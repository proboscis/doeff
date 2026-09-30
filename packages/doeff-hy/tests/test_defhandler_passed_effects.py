"""柵の形の defhandler(総受けの節が「X でなければ」の番)を、VM が X の effect で飛ばす事の検(agora-redesign #2008)。

節が EffectBase の総受けで、番が `(not (isinstance effect X))` の handler は、それより前の節が名指さない X の effect を
`(Pass effect k)` で外へ渡すだけ。型の註では「X 以外の全部」を言えないので、VM は全部の effect でこの handler に入っていた。
defhandler はこの形を見つけると、X と前の節の型を install の時に VM へ渡し(doeff_vm._effect_types.declare_passes)、VM は
そういう effect でこの handler を Python に入らずに飛ばす — 番が外れた時と同じ終わり方なので、答えと順は変わらない。
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from types import CodeType, FrameType

import pytest
from doeff_vm._effect_types import PassedEffects, handler_spec

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

;; 柵の形: 前の節 Ping・総受けは passable の外を断る
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
    """defhandler の値から、VM が据える handler の関数(`__doeff_handler_data__`)。"""
    return handler_fn.__doeff_handler_data__  # type: ignore[attr-defined] — defhandler の値が持つ欄(handle.hy)


def _entries(code: CodeType, run: Callable[[], object]) -> tuple[int, object]:
    """run の間に code の関数が始まった回数と、run の答え。本体は generator なので再開ごとにも call の事象が出る — frame の同一性で数える。"""
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
    assert entered == 1, "Pong 3 回は柵に入らずに外の pongs へ届く・前の節の型 Ping は柵が答える"


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
;; 番の型が handler より後に書いた定数(install の時に解く)
(defhandler gate
  (EffectBase []
    :when (not (isinstance effect LATER))
    (raise (ValueError "gated"))))
;; 番が別の形 — 飛ばさない(全部の effect で入る)
(defhandler custom
  (EffectBase []
    :when (not (= (. (type effect) __name__) "Pong"))
    (raise (ValueError "custom"))))
(setv LATER Pong)
""")
    assert handler_spec(_data(ns["gate"])).passed == PassedEffects((ns["Pong"],), ())
    assert handler_spec(_data(ns["custom"])).passed is None
