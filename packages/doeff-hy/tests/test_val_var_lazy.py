"""val・var・lazy val・lazy var・session val・session var と := の検(ADR-DOE-HY-006)。

Hy の断片を module に展開して動かす。展開の誤りは HyMacroExpansionError、所見(setv の警告・束縛し直し)は
doeff-hy-check と同じ集め方(static_view.collect_findings の中で型検査のための展開をする)で確かめる。
"""

from __future__ import annotations

import sys
import types
import warnings

import doeff_hy  # noqa: F401 - Hy の import hook を入れる(断片の展開に要る)
import hy
import hy.compiler
import pytest
from hy.errors import HyMacroExpansionError

from doeff_hy.binding_forms import RULE_LEGACY_LAZY, RULE_REBIND, RULE_SETV, Finding
from doeff_hy.static_view import collect_findings, static_view

PRELUDE = """
(require doeff-hy.macros [defk deftest defp <- set! defhandler with-handler])
(import doeff [run EffectBase Some])
(import doeff_core_effects [Get Put state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_hy.session [session-key])
(import dataclasses [dataclass])

(defclass [(dataclass :frozen True)] Probe [EffectBase] #^ str tag)
(defclass [(dataclass :frozen True)] Boom [EffectBase] #^ str tag)

;; Probe の実行を log に残して tag を返す。Boom は最初の n 回だけ落ちる。
(defhandler probe-handler [log fails]
  (Probe [tag] (.append log tag) (resume tag))
  (Boom [tag]
    (.append log tag)
    (if (> (get fails 0) 0)
        (do (setv (get fails 0) (- (get fails 0) 1)) (raise (ValueError "boom")))
        (resume tag))))

(defn run-probe [program [fails 0]]
  (setv log [])
  (setv result (run ((probe-handler log [fails]) program)))
  #(result log))

(defn run-state [program [store None]]
  (run (scheduled ((state :initial (or store {})) program))))
"""


def _module(code: str, name: str) -> types.ModuleType:
    """断片を新しい module に展開して実行する(__name__ は name)。"""
    module = types.ModuleType(name)
    module.__file__ = f"<{name}>"
    sys.modules[name] = module
    hy.eval(hy.read_many(PRELUDE + code), module=module)
    return module


def _expansion_error(code: str) -> str:
    """断片の展開が誤りになることを確かめ、その文を返す。"""
    with pytest.raises(HyMacroExpansionError) as caught:
        _module(code, "_vvl_error")
    return str(caught.value)


def _findings(code: str) -> list[Finding]:
    """doeff-hy-check と同じく、型検査のための展開の中で所見を集める。"""
    module = types.ModuleType("_vvl_findings")
    with static_view(), collect_findings() as found:
        hy.compiler.hy_compile(hy.read_many(PRELUDE + code), module)
    return list(found)


# ---------------------------------------------------------------------------
# val / var / :=
# ---------------------------------------------------------------------------


def test_val_binds_once_and_second_val_is_an_expansion_error() -> None:
    module = _module(
        """
(defk twice [x]
  {:pre [(: x int)] :post [(: % int)]}
  (val y (+ x 1))
  (* y 2))
(setv result (run (twice 3)))
""",
        "_vvl_val",
    )
    assert module.result == 8
    message = _expansion_error(
        """
(defk twice [x]
  {:pre [(: x int)] :post [(: % int)]}
  (val y 1)
  (val y 2)
  y)
"""
    )
    assert "(val y …) で宣言済み" in message


def test_rebinding_a_val_by_setv_or_colon_equals_is_an_expansion_error() -> None:
    assert "束縛し直しています" in _expansion_error(
        """
(defk f [x]
  {:pre [(: x int)] :post [(: % int)]}
  (val y 1)
  (setv y 2)
  y)
"""
    )
    assert "書き換えられません" in _expansion_error(
        """
(defk f [x]
  {:pre [(: x int)] :post [(: % int)]}
  (val y 1)
  (:= y 2)
  y)
"""
    )
    assert "宣言されていません" in _expansion_error(
        """
(defk f [x]
  {:pre [(: x int)] :post [(: % int)]}
  (:= z 2)
  z)
"""
    )


def test_var_is_rewritten_with_colon_equals() -> None:
    module = _module(
        """
(defk total [xs]
  {:pre [(: xs list)] :post [(: % int)]}
  (var acc 0)
  (for [x xs]
    (:= acc (+ acc x)))
  acc)
(setv result (run (total [1 2 3 4])))
""",
        "_vvl_var",
    )
    assert module.result == 10


def test_bang_written_as_two_tokens_is_the_same_as_the_bang_form() -> None:
    """Hy の reader は !(f) を ! と (f) の 2 つに読む — 宣言と := の中では (! (f)) と同じ。"""
    module = _module(
        """
(defk a []
  {:pre [] :post [(: % str)]}
  (val x !(Probe "one"))
  (var y (! (Probe "two")))
  (:= y !(Probe "three"))
  (+ x y))
(setv #(result log) (run-probe (a)))
""",
        "_vvl_bang",
    )
    assert module.result == "onethree"
    assert module.log == ["one", "two", "three"]
    # 実測の記録: reader は !(f) を Symbol('!') と Expression([f]) の 2 つに読む。
    read = hy.read("(val x !(f))")
    assert [type(part).__name__ for part in read] == ["Symbol", "Symbol", "Symbol", "Expression"]
    assert str(read[2]) == "!"


# ---------------------------------------------------------------------------
# lazy val / lazy var
# ---------------------------------------------------------------------------

LAZY = """
(defk lazy-probe [uses]
  {:pre [(: uses int)] :post [(: % list)]}
  (lazy val x !(Probe "init"))
  (var out [])
  (for [_ (range uses)]
    (:= out (+ out [x])))
  out)
"""


def test_lazy_val_is_not_evaluated_when_unused() -> None:
    module = _module(LAZY + '(setv #(result log) (run-probe (lazy-probe 0)))', "_vvl_lazy0")
    assert module.result == []
    assert module.log == []


def test_lazy_val_used_twice_is_evaluated_once() -> None:
    module = _module(LAZY + '(setv #(result log) (run-probe (lazy-probe 3)))', "_vvl_lazy3")
    assert module.result == ["init", "init", "init"]
    assert module.log == ["init"]


def test_lazy_val_is_evaluated_again_in_a_new_call() -> None:
    module = _module(
        LAZY
        + """
(defk two-calls []
  {:pre [] :post [(: % list)]}
  (+ (! (lazy-probe 2)) (! (lazy-probe 2))))
(setv #(result log) (run-probe (two-calls)))
""",
        "_vvl_lazy_calls",
    )
    assert module.result == ["init"] * 4
    assert module.log == ["init", "init"]


def test_lazy_val_whose_first_evaluation_raised_is_evaluated_again() -> None:
    module = _module(
        """
(defk retry []
  {:pre [] :post [(: % str)]}
  (lazy val x !(Boom "try"))
  (var first "")
  (try
    (:= first x)
    (except [e ValueError]
      (:= first "failed")))
  (+ first "/" x))
(setv #(result log) (run-probe (retry) :fails 1))
""",
        "_vvl_lazy_retry",
    )
    assert module.result == "failed/try"
    assert module.log == ["try", "try"]


def test_lazy_var_assigned_before_first_use_never_evaluates_the_initial_expression() -> None:
    module = _module(
        """
(defk skip-init [flag]
  {:pre [(: flag bool)] :post [(: % str)]}
  (lazy var x !(Probe "init"))
  (when flag
    (:= x "given"))
  (:= x (+ x "!"))
  x)
(setv #(given given-log) (run-probe (skip-init True)))
(setv #(used used-log) (run-probe (skip-init False)))
""",
        "_vvl_lazy_var",
    )
    assert (module.given, module.given_log) == ("given!", [])
    assert (module.used, module.used_log) == ("init!", ["init"])


def test_lazy_reference_inside_fn_or_comprehension_is_an_expansion_error() -> None:
    for inner in ["(list (map (fn [y] (+ x y)) [\"a\"]))", "(lfor y [\"a\"] (+ x y))"]:
        message = _expansion_error(
            f"""
(defk bad []
  {{:pre [] :post [(: % list)]}}
  (lazy val x !(Probe "init"))
  {inner})
"""
        )
        assert "先に本体で取り出してから" in message
        assert "(val x-value x)" in message


def test_shadowing_a_lazy_name_is_an_expansion_error() -> None:
    assert "影" in _expansion_error(
        """
(defk bad []
  {:pre [] :post [(: % list)]}
  (lazy val x !(Probe "init"))
  (for [x [1 2]] (print x))
  [])
"""
    )


# ---------------------------------------------------------------------------
# 旧い lazy は defk で誤り・session は defk / deftest で誤り
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "legacy",
    [
        '(lazy-val client "c")',
        '(lazy client "c")',
        '(lazy-var client "c")',
    ],
)
def test_legacy_lazy_in_defk_is_an_expansion_error_that_guides_to_the_new_forms(legacy: str) -> None:
    message = _expansion_error(
        f"""
(defk old []
  {{:pre [] :post [(: % str)]}}
  {legacy}
  client)
"""
    )
    assert "(lazy val client 式)" in message
    assert "(session val client 式)" in message


def test_session_in_defk_and_deftest_is_an_expansion_error() -> None:
    assert "defhandler にだけ書けます" in _expansion_error(
        """
(defk f []
  {:pre [] :post [(: % str)]}
  (session val client "c")
  client)
"""
    )
    assert "defhandler にだけ書けます" in _expansion_error(
        """
(deftest test-f
  (session var n 0)
  (assert (= n 0)))
"""
    )


# ---------------------------------------------------------------------------
# defhandler: session val / session var と旧い lazy-val / lazy-var / set!
# ---------------------------------------------------------------------------

SESSION = """
(defclass [(dataclass :frozen True)] Ask-client [EffectBase])
(defclass [(dataclass :frozen True)] Bump [EffectBase])

(defhandler client-handler [made]
  (session val client (do (.append made "made") "client"))
  (session var count 0)
  (Ask-client [] (resume client))
  (Bump []
    (:= count (+ count 1))
    (resume count)))

(defhandler legacy-handler [made]
  (lazy-val client (do (.append made "made") "client"))
  (lazy-var count 0)
  (Ask-client [] (resume client))
  (Bump []
    (set! count (+ count 1))
    (resume count)))

(defp body {:post [(: % list)]}
  (<- a (Ask-client))
  (<- b (Ask-client))
  (<- c1 (Bump))
  (<- c2 (Bump))
  [a b c1 c2])
"""


def test_session_val_and_var_behave_like_the_legacy_lazy_val_and_var() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        module = _module(
            SESSION
            + """
(setv made-new [])
(setv made-old [])
(setv new-result (run-state ((client-handler made-new) body)))
(setv old-result (run-state ((legacy-handler made-old) body)))
""",
            "_vvl_session",
        )
    assert module.new_result == module.old_result == ["client", "client", 1, 2]
    assert module.made_new == module.made_old == ["made"]
    deprecations = [str(w.message) for w in caught if issubclass(w.category, DeprecationWarning)]
    assert any("(session val client …)" in text for text in deprecations)
    assert any("(session var count …)" in text for text in deprecations)
    assert any("(:= count 新しい値)" in text for text in deprecations)


def test_session_key_is_the_same_as_the_legacy_key() -> None:
    """旧い形から session へ書き換えても、同じセッションの値は引き継がれる(同じキー)。"""
    module = _module(
        SESSION
        + """
(setv made [])
(setv key (session-key "_vvl_session_key" "client-handler" "count"))
(setv result (run-state ((client-handler made) body) {key (Some 40)}))
""",
        "_vvl_session_key",
    )
    assert module.key == "_vvl_session_key/client-handler/count"
    assert module.result == ["client", "client", 41, 42]


def test_outer_handler_answering_get_skips_the_session_initialiser_and_sees_put() -> None:
    module = _module(
        SESSION
        + """
(setv writes [])
(defhandler steer-session []
  (Get [key]
    :when (= key (session-key __name__ "client-handler" "client"))
    (resume (Some "scripted-client")))
  (Put [key value]
    :when (= key (session-key __name__ "client-handler" "count"))
    (.append writes (. value value))
    (reperform effect)))
(setv made [])
(setv result
  (run-state ((steer-session) ((client-handler made) body))))
""",
        "_vvl_session_outer",
    )
    assert module.result == ["scripted-client", "scripted-client", 1, 2]
    assert module.made == []
    # 初めて使った時の初期値の書き込み(0)も、以後の := の書き込みも、同じ Put として外から見える。
    assert module.writes == [0, 1, 2]


def test_session_val_cannot_be_rewritten() -> None:
    assert "session val count" in _expansion_error(
        """
(defclass [(dataclass :frozen True)] Bump [EffectBase])
(defhandler h
  (session val count 0)
  (Bump [] (:= count 1) (resume count)))
"""
    )


def test_lazy_val_at_handler_level_points_to_session_or_clause() -> None:
    assert "session val client" in _expansion_error(
        """
(defclass [(dataclass :frozen True)] Bump [EffectBase])
(defhandler h
  (lazy val client "c")
  (Bump [] (resume client)))
"""
    )


def test_clause_local_val_var_and_lazy() -> None:
    module = _module(
        """
(defclass [(dataclass :frozen True)] Twice [EffectBase] #^ int n)
(defhandler twice-handler [log fails]
  (Twice [n]
    (val base (* n 2))
    (lazy val unused !(Probe "never"))
    (var out base)
    (:= out (+ out 1))
    (resume out)))
(defk use []
  {:pre [] :post [(: % int)]}
  (! (Twice 5)))
(setv log [])
(setv result (run ((probe-handler log [0]) ((twice-handler [] [0]) (use)))))
""",
        "_vvl_clause",
    )
    assert module.result == 11
    assert module.log == []


# ---------------------------------------------------------------------------
# deftest でも同じに効く
# ---------------------------------------------------------------------------


def test_deftest_supports_val_var_and_lazy() -> None:
    module = _module(
        """
(deftest test-inner
  (lazy val x !(Probe "init"))
  (val y (+ x x))
  (var z y)
  (:= z (+ z "!"))
  (assert (= z "initinit!")))
""",
        "_vvl_deftest",
    )
    log: list[str] = []

    def interpreter(program: object) -> object:
        return module.run(module.probe_handler(log, [0])(program))

    module.test_inner(interpreter)
    assert log == ["init"]


# ---------------------------------------------------------------------------
# 所見: setv の警告・束縛し直し(for の変数は数えない・排他の枝は数えない)
# ---------------------------------------------------------------------------


def test_setv_in_a_body_is_warned_to_use_val_or_var() -> None:
    found = _findings(
        """
(defk f [x]
  {:pre [(: x int)] :post [(: % int)]}
  (setv y (+ x 1))
  (setv (get {} "k") 1)
  (list (map (fn [a] (setv b a) b) [1]))
  y)
"""
    )
    setv = [f for f in found if f.rule == RULE_SETV]
    assert len(setv) == 1
    assert "(val y …)" in setv[0].message
    assert setv[0].severity.value == "warning"


def test_rebinding_is_red_but_loop_variables_and_exclusive_branches_are_not() -> None:
    found = _findings(
        """
(defk f [flag xs]
  {:pre [(: flag bool) (: xs list)] :post [(: % int)]}
  (for [x xs] (print x))
  (for [x xs] (print x))
  (if flag (setv r 1) (setv r 2))
  (setv total 0)
  (for [x xs] (setv total (+ total x)))
  (<- got (Probe "a"))
  (<- got (Probe "b"))
  (+= total r)
  total)
"""
    )
    rebinds = sorted((f.line, f.message.split("`")[1]) for f in found if f.rule == RULE_REBIND)
    names = [name for _, name in rebinds]
    assert names.count("total") == 2  # ループの中の setv と +=
    assert names.count("got") == 1
    assert "x" not in names and "r" not in names
    assert all(f.severity.value == "error" for f in found if f.rule == RULE_REBIND)


def test_rebinding_is_counted_in_deftest_and_handler_clauses_and_legacy_set_bang_is_noted() -> None:
    found = _findings(
        """
(deftest test-g
  (setv a 1)
  (setv a 2)
  (assert a))
(defclass [(dataclass :frozen True)] Bump [EffectBase])
(defhandler h
  (lazy-var count 0)
  (Bump []
    (setv v 1)
    (setv v 2)
    (set! count (+ count v))
    (resume count)))
"""
    )
    assert sum(1 for f in found if f.rule == RULE_REBIND) == 2
    legacy = [f for f in found if f.rule == RULE_LEGACY_LAZY]
    assert any("(lazy-var count …) は (session var count …)" in f.message for f in legacy)
    assert any("(set! count …) は (:= count 新しい値)" in f.message for f in legacy)


# ---------------------------------------------------------------------------
# module の直下の val / var / lazy val
# ---------------------------------------------------------------------------

MODULE_PRELUDE = "(require doeff-hy.macros [val var lazy session])\n"


def test_module_val_binds_once_and_second_val_is_an_expansion_error() -> None:
    module = _module(MODULE_PRELUDE + "(val limit 3)\n(val doubled (* limit 2))", "_vvl_module_val")
    assert (module.limit, module.doubled) == (3, 6)
    assert "(val limit …) で宣言済み" in _expansion_error(MODULE_PRELUDE + "(val limit 3)\n(val limit 4)")


def test_module_lazy_val_is_evaluated_on_first_use_only() -> None:
    module = _module(
        MODULE_PRELUDE
        + """
(setv made [])
(defn build-table [] (.append made "built") {"a" 1})
(lazy val table (build-table))
(defk lookup [key]
  {:pre [(: key str)] :post [(: % int)]}
  (get table key))
(setv before (list made))
(setv first (run (lookup "a")))
(setv second (run (lookup "a")))
""",
        "_vvl_module_lazy",
    )
    assert module.before == []
    assert (module.first, module.second) == (1, 1)
    assert module.made == ["built"]
    # 他の module からの属性の参照(PEP 562)も同じ値。
    assert module.table == {"a": 1}
    assert module.made == ["built"]


def test_module_lazy_val_with_an_effect_or_lazy_var_is_an_expansion_error() -> None:
    message = _expansion_error(MODULE_PRELUDE + '(lazy val client !(Probe "x"))')
    assert "(session val client 式)" in message
    assert "handler が無く" in message
    message = _expansion_error(MODULE_PRELUDE + "(lazy var counter 0)")
    assert "(var counter 式)" in message and "(session var counter 式)" in message


def test_val_inside_defn_is_an_expansion_error() -> None:
    assert "defn・fn・class・let の中には書けません" in _expansion_error(
        MODULE_PRELUDE + "(defn f [] (val x 1) x)"
    )


def test_colon_equals_on_a_module_var_inside_defk_is_an_expansion_error() -> None:
    assert "module の var" in _expansion_error(
        MODULE_PRELUDE
        + """
(var counter 0)
(defk bump []
  {:pre [] :post [(: % int)]}
  (:= counter (+ counter 1))
  counter)
"""
    )


def test_module_level_setv_is_warned_and_rebinding_a_module_val_is_red() -> None:
    from doeff_hy.binding_forms import module_findings

    found = module_findings(
        hy.read_many(
            """
(setv limit 3)
(val size 4)
(var total 0)
(setv total 1)
(setv size 5)
(:= total 2)
(when True (setv flag True))
(defn f [] (setv inner 1) inner)
"""
        )
    )
    setv = sorted(f.line for f in found if f.rule == RULE_SETV)
    # limit と flag。var の total・defn の中は警告しない。val の size の setv は警告ではなく赤(下)。
    assert setv == [2, 8]
    red = sorted(f.line for f in found if f.rule == RULE_REBIND)
    assert red == [6, 7]  # val の size の setv・効かない :=
