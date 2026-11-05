"""Tests for val, var, lazy val, lazy var, session val, session var and := (ADR-DOE-HY-006).

Hy fragments are expanded into a module and run. Expansion errors are HyMacroExpansionError; findings (setv warnings, rebinding) are
checked the same way as doeff-hy-check (static_view.collect_findings expands for type checking).
"""

from __future__ import annotations

import sys
import types
import warnings
from pathlib import Path

import doeff_hy  # noqa: F401 - installs the Hy import hook (needed to expand the fragments)
import hy
import hy.compiler
import pytest
from hy.errors import HyMacroExpansionError

from doeff_hy._binding_forms import RULE_LEGACY_LAZY, RULE_REBIND, RULE_SETV, Finding
from doeff_hy._static_check import Projection, project
from doeff_hy._static_view import collect_findings, static_view

PRELUDE = """
(require doeff-hy.macros [defk deftest defp <- set! defhandler with-handler])
(import doeff [run EffectBase Some])
(import doeff_core_effects [Get Put state])
(import doeff_core_effects._scheduler [scheduled])
(import doeff_hy._session [session-key])
(import dataclasses [dataclass])

(defclass [(dataclass :frozen True)] Probe [EffectBase] #^ str tag)
(defclass [(dataclass :frozen True)] Boom [EffectBase] #^ str tag)

;; Log the Probe run and return the tag. Boom fails only the first n times.
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
    """Expand the fragment into a fresh module and run it (__name__ is name)."""
    module = types.ModuleType(name)
    module.__file__ = f"<{name}>"
    sys.modules[name] = module
    hy.eval(hy.read_many(PRELUDE + code), module=module)
    return module


def _expansion_error(code: str) -> str:
    """Check that expanding the fragment is an error, and return its message."""
    with pytest.raises(HyMacroExpansionError) as caught:
        _module(code, "_vvl_error")
    return str(caught.value)


def _findings(code: str) -> list[Finding]:
    """Collect findings inside the type-checking expansion, the same as doeff-hy-check."""
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
    assert "is already declared with (val y …)" in message


def test_rebinding_a_val_by_setv_or_colon_equals_is_an_expansion_error() -> None:
    assert "it is rebound here" in _expansion_error(
        """
(defk f [x]
  {:pre [(: x int)] :post [(: % int)]}
  (val y 1)
  (setv y 2)
  y)
"""
    )
    assert "cannot be rewritten" in _expansion_error(
        """
(defk f [x]
  {:pre [(: x int)] :post [(: % int)]}
  (val y 1)
  (:= y 2)
  y)
"""
    )
    assert "is not declared" in _expansion_error(
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
    """The Hy reader reads !(f) as two tokens, ! and (f) -- inside a declaration and := it is the same as (! (f))."""
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
    # Measured record: the reader reads !(f) as two forms, Symbol('!') and Expression([f]).
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
        assert "Fetch it in the body first" in message
        assert "(val x-value x)" in message


def test_shadowing_a_lazy_name_is_an_expansion_error() -> None:
    assert "shadow" in _expansion_error(
        """
(defk bad []
  {:pre [] :post [(: % list)]}
  (lazy val x !(Probe "init"))
  (for [x [1 2]] (print x))
  [])
"""
    )


# ---------------------------------------------------------------------------
# The old lazy is an error in defk; session is an error in defk / deftest
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
    assert "(lazy val client expr)" in message
    assert "(session val client expr)" in message


def test_session_in_defk_and_deftest_is_an_expansion_error() -> None:
    assert "can be written only in defhandler" in _expansion_error(
        """
(defk f []
  {:pre [] :post [(: % str)]}
  (session val client "c")
  client)
"""
    )
    assert "can be written only in defhandler" in _expansion_error(
        """
(deftest test-f
  (session var n 0)
  (assert (= n 0)))
"""
    )


# ---------------------------------------------------------------------------
# defhandler: session val / session var and the old lazy-val / lazy-var / set!
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
    assert any("(:= count new-value)" in text for text in deprecations)


def test_session_key_is_the_same_as_the_legacy_key() -> None:
    """Even when rewriting from the old form to session, the session value is carried over (same key)."""
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
    # The initial-value write on first use (0) and the later := writes are both visible from outside as the same Put.
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
# Works the same in deftest
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
# Findings: setv warnings and rebinding (a for variable is not counted; exclusive branches are not counted)
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
    assert names.count("total") == 2  # setv and += inside the loop
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
    assert any("(lazy-var count …) should be moved to (session var count …)" in f.message for f in legacy)
    assert any("(set! count …) should be moved to (:= count new-value)" in f.message for f in legacy)


# ---------------------------------------------------------------------------
# val / var / lazy val directly under a module
# ---------------------------------------------------------------------------

MODULE_PRELUDE = "(require doeff-hy.macros [val var lazy session])\n"


def test_module_val_binds_once_and_second_val_is_an_expansion_error() -> None:
    module = _module(MODULE_PRELUDE + "(val limit 3)\n(val doubled (* limit 2))", "_vvl_module_val")
    assert (module.limit, module.doubled) == (3, 6)
    assert "is already declared with (val limit …)" in _expansion_error(MODULE_PRELUDE + "(val limit 3)\n(val limit 4)")


def test_module_val_and_var_take_a_type_annotation() -> None:
    # Annotation for when the value type cannot be decided from the expression (e.g. an empty dict) -- `(val #^ type name expr)`. At runtime it is an annotated assignment.
    module = _module(
        MODULE_PRELUDE
        + """
(import re)
(val #^ (get dict #(str int)) memo {})
(val #^ (get re.Pattern str) token (re.compile "a+"))
(var #^ int total 0)
(setv total 3)
""",
        "_vvl_module_annotated",
    )
    assert module.memo == {} and module.token.pattern == "a+" and module.total == 3
    assert set(module.__annotations__) == {"memo", "token", "total"}


def test_an_annotated_module_val_keeps_the_annotation_in_the_type_check_expansion(tmp_path: Path) -> None:
    source = tmp_path / "annotated_probe.hy"
    source.write_text(
        MODULE_PRELUDE + "(val #^ (get dict #(str int)) memo {})\n(var #^ int total 0)\n(val plain 1)\n", encoding="utf-8"
    )
    projection = project(tmp_path, [tmp_path], source)
    assert isinstance(projection, Projection)
    lines = projection.text.splitlines()
    assert "memo: dict[str, int] = {}" in lines and "total: int = 0" in lines and "plain = 1" in lines


def test_an_annotation_is_refused_on_lazy_and_session_declarations() -> None:
    for form in ("(lazy val #^ int size 3)", "(lazy var #^ int size 3)", "(session val #^ int size 3)"):
        assert "the type annotation `#^ type name` is accepted only by (val …) and (var …)" in _expansion_error(MODULE_PRELUDE + form)
    # Even with an annotation, redefining a name is still refused as before.
    assert "is already declared with (val limit …)" in _expansion_error(
        MODULE_PRELUDE + "(val #^ int limit 3)\n(val limit 4)"
    )


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
    # Attribute access from another module (PEP 562) gives the same value.
    assert module.table == {"a": 1}
    assert module.made == ["built"]


def test_module_lazy_val_with_an_effect_or_lazy_var_is_an_expansion_error() -> None:
    message = _expansion_error(MODULE_PRELUDE + '(lazy val client !(Probe "x"))')
    assert "(session val client expr)" in message
    assert "has no handler" in message
    message = _expansion_error(MODULE_PRELUDE + "(lazy var counter 0)")
    assert "(var counter expr)" in message and "(session var counter expr)" in message


def test_val_inside_defn_is_an_expansion_error() -> None:
    assert "not inside defn, fn, class or let" in _expansion_error(
        MODULE_PRELUDE + "(defn f [] (val x 1) x)"
    )


def test_colon_equals_on_a_module_var_inside_defk_is_an_expansion_error() -> None:
    assert "is a module var" in _expansion_error(
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
    from doeff_hy._binding_forms import module_findings

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
    # limit and flag. No warning for var total or inside defn. The setv of val size is red (below), not a warning.
    assert setv == [2, 8]
    red = sorted(f.line for f in found if f.rule == RULE_REBIND)
    assert red == [6, 7]  # setv of val size, and a := that has no effect


# ---------------------------------------------------------------------------
# The :when guard of a clause and session values (the guard is evaluated after the session values are taken out)
# ---------------------------------------------------------------------------

GUARDED = """
(defclass [(dataclass :frozen True)] Take [EffectBase] #^ int n)
(defclass [(dataclass :frozen True)] Ping [EffectBase])

;; Read limit (session val) and used (session var) in the :when guard. An effect whose guard fails is passed to the outer handler.
(defhandler budget-handler [made]
  (session val limit (do (.append made "limit") 2))
  (session var used 0)
  (Take [n]
    :when (<= (+ used n) limit)
    (:= used (+ used n))
    (resume used))
  (Ping [] (resume "pong")))

(defhandler legacy-budget-handler [made]
  (lazy-val limit (do (.append made "limit") 2))
  (lazy-var used 0)
  (Take [n]
    :when (<= (+ used n) limit)
    (set! used (+ used n))
    (resume used)))

;; The outer handler that receives the Take whose guard failed (returns -1).
(defhandler overflow []
  (Take [n] (resume -1)))

(defp takes {:post [(: % list)]}
  (<- a (Take 1))
  (<- b (Take 1))
  (<- c (Take 1))
  [a b c])

(defp pings {:post [(: % str)]}
  (<- p (Ping))
  p)
"""


def test_when_guard_reads_session_values_taken_before_the_guard() -> None:
    """If the :when guard reads a session val / session var, they are taken out before the guard (the old form evaluated the guard before taking them out,
    and the guard's name was undefined = UnboundLocalError). The third time the guard fails and the effect is passed to the outer handler."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        module = _module(
            GUARDED
            + """
(setv made-new [])
(setv made-old [])
(setv new-result (run-state ((overflow) ((budget-handler made-new) takes))))
(setv old-result (run-state ((overflow) ((legacy-budget-handler made-old) takes))))
""",
            "_vvl_guard_session",
        )
    assert module.new_result == module.old_result == [1, 2, -1]
    assert module.made_new == module.made_old == ["limit"]


def test_session_value_used_only_in_another_clause_is_not_initialised_by_the_guard() -> None:
    """A session value the guard does not read is, as before, created for the first time only in the clause whose body uses it (not taken out before the guard)."""
    module = _module(
        GUARDED + "(setv made [])\n(setv result (run-state ((budget-handler made) pings)))\n",
        "_vvl_guard_untouched",
    )
    assert module.result == "pong"
    assert module.made == []
