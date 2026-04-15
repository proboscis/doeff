"""TDD red-phase tests for #391: lazy clause + <-> threading macro.

Tests:
  1. lazy clause in defhandler — per-session init via Get/Put + Some/Nothing
  2. lazy clause in defk — same mechanism
  3. <-> effectful threading macro
"""
import pytest

from doeff import do, run as doeff_run, WithHandler, EffectBase, Some, Nothing
from doeff_core_effects.effects import Ask, Get, Put
from doeff_core_effects.handlers import state, lazy_ask
from doeff_core_effects.scheduler import scheduled
from dataclasses import dataclass


# ── Test effects ──────────────────────────────────────────────────

@dataclass(frozen=True)
class FetchData(EffectBase):
    source: str


@dataclass(frozen=True)
class Transform(EffectBase):
    data: object
    param: int


# ── Helpers ───────────────────────────────────────────────────────

def run_with(program, env=None, store=None):
    """Run a program with lazy_ask + state + scheduler."""
    wrapped = program
    handlers = [
        lazy_ask(env=env or {}),
        state(initial=store or {}),
    ]
    for h in reversed(handlers):
        wrapped = WithHandler(h, wrapped)
    wrapped = scheduled(wrapped)
    return doeff_run(wrapped)


# ═══════════════════════════════════════════════════════════════════
# 1. lazy clause in defhandler — macro-level
# ═══════════════════════════════════════════════════════════════════

class TestLazyHandlerMacro:
    """Test lazy clause parsing and AST expansion in defhandler."""

    def test_lazy_handler_basic(self):
        """Lazy init runs on first effect, uses cached value on second."""
        import hy  # noqa: F401
        import doeff_hy  # noqa: F401

        # This Hy code uses the lazy clause in defhandler
        code = """
(require doeff-hy.macros [defk defp <-])
(require doeff-hy.handle [defhandler])
(import doeff [do :as _doeff-do EffectBase WithHandler Some Nothing run :as doeff-run])
(import doeff_core_effects [Ask Get Put state])
(import doeff_core_effects.handlers [lazy-ask])
(import doeff_core_effects.scheduler [scheduled])
(import dataclasses [dataclass])

(defclass [(dataclass :frozen True)] MyFetch [EffectBase]
  #^ str source)

(defhandler my-handler
  (lazy client
    (<- ep (Ask "endpoint"))
    (+ "client:" ep))

  (MyFetch [source]
    (resume (+ client ":" source))))

(defp body {:post [(: % list)]}
  (<- r1 (MyFetch :source "a"))
  (<- r2 (MyFetch :source "b"))
  [r1 r2])

(setv wrapped body)
(setv wrapped (WithHandler my-handler wrapped))
(for [h (reversed [(lazy-ask :env {"endpoint" "localhost"}) (state)])]
  (setv wrapped (WithHandler h wrapped)))
(setv wrapped (scheduled wrapped))
(setv _result (doeff-run wrapped))
(assert (= _result ["client:localhost:a" "client:localhost:b"]))
"""
        hy.eval(hy.read_many(code), module=__name__)
        # Can't easily extract result from hy.eval in this pattern,
        # so test via direct module-level execution instead.

    def test_lazy_handler_runs_once(self):
        """Lazy init should execute only once even with multiple effect invocations."""
        import hy  # noqa: F401
        import doeff_hy  # noqa: F401

        # We test by checking state key contains Some after first init
        @do
        def handler_with_lazy(effect, k):
            """Simulates what the lazy macro should expand to."""
            from doeff.program import Resume, Pass
            if isinstance(effect, FetchData):
                # --- This is what lazy macro should generate ---
                cached = yield Get(f"{__name__}/handler_with_lazy/client")
                if isinstance(cached, Some):
                    client = cached.value
                else:
                    # init body
                    client = "initialized-client"
                    yield Put(f"{__name__}/handler_with_lazy/client", Some(client))
                # --- end lazy expansion ---
                result = yield Resume(k, f"{client}:{effect.source}")
                return result
            yield Pass(effect, k)

        @do
        def body():
            r1 = yield FetchData(source="a")
            r2 = yield FetchData(source="b")
            return [r1, r2]

        result = run_with(
            WithHandler(handler_with_lazy, body()),
        )
        assert result == ["initialized-client:a", "initialized-client:b"]

    def test_lazy_handler_none_value_via_some(self):
        """Lazy init that returns None should be cached as Some(None)."""
        init_count = [0]

        @do
        def handler_with_lazy_none(effect, k):
            from doeff.program import Resume, Pass
            if isinstance(effect, FetchData):
                cached = yield Get(f"{__name__}/handler/val")
                if isinstance(cached, Some):
                    val = cached.value
                else:
                    init_count[0] += 1
                    val = None
                    yield Put(f"{__name__}/handler/val", Some(val))
                result = yield Resume(k, val)
                return result
            yield Pass(effect, k)

        @do
        def body():
            r1 = yield FetchData(source="a")
            r2 = yield FetchData(source="b")
            return [r1, r2]

        result = run_with(
            WithHandler(handler_with_lazy_none, body()),
        )
        assert result == [None, None]
        assert init_count[0] == 1, "Init should run exactly once, not twice"


# ═══════════════════════════════════════════════════════════════════
# 2. lazy clause in defhandler — full Hy macro integration
# ═══════════════════════════════════════════════════════════════════

class TestLazyHandlerHyIntegration:
    """Test that the actual defhandler lazy clause Hy macro works end-to-end."""

    def test_defhandler_lazy_compiles(self):
        """(defhandler name (lazy x ...) (Effect [f] ...)) should compile."""
        import hy
        import doeff_hy  # noqa: F401

        # This should NOT raise a SyntaxError once implemented
        code = """
(require doeff-hy.handle [defhandler])
(import doeff [EffectBase])
(import dataclasses [dataclass])

(defclass [(dataclass :frozen True)] Ping [EffectBase])

(defhandler ping-handler
  (lazy greeting "hello")
  (Ping [] (resume greeting)))
"""
        hy.eval(hy.read_many(code))

    def test_defhandler_lazy_with_effects(self):
        """Lazy init body can perform effects (Ask) for config."""
        import hy
        import doeff_hy  # noqa: F401

        code = """
(require doeff-hy.macros [defp <-])
(require doeff-hy.handle [defhandler])
(import doeff [do :as _doeff-do EffectBase WithHandler run :as doeff-run])
(import doeff_core_effects [Ask Get Put state])
(import doeff_core_effects.handlers [lazy-ask])
(import doeff_core_effects.scheduler [scheduled])
(import dataclasses [dataclass])

(defclass [(dataclass :frozen True)] Greet [EffectBase]
  #^ str name)

(defhandler greet-handler
  (lazy prefix
    (<- p (Ask "prefix"))
    (+ p ":"))
  (Greet [name]
    (resume (+ prefix name))))

(defp body {:post [(: % list)]}
  (<- r1 (Greet :name "Alice"))
  (<- r2 (Greet :name "Bob"))
  [r1 r2])

(setv wrapped body)
(setv wrapped (WithHandler greet-handler wrapped))
(for [h (reversed [(lazy-ask :env {"prefix" "Hi"}) (state)])]
  (setv wrapped (WithHandler h wrapped)))
(setv wrapped (scheduled wrapped))
(setv __test_result__ (doeff-run wrapped))
"""
        import types
        mod = types.ModuleType("_test_lazy_hy")
        mod.__file__ = "<test>"
        import sys
        sys.modules["_test_lazy_hy"] = mod
        try:
            hy.eval(hy.read_many(code), module=mod)
            assert mod.__test_result__ == ["Hi:Alice", "Hi:Bob"]
        finally:
            del sys.modules["_test_lazy_hy"]

    def test_defhandler_lazy_key_contains_module(self):
        """Lazy state key should be prefixed with module name."""
        import hy
        import doeff_hy  # noqa: F401
        import types

        observed_keys = []

        # Create a spy state handler that records Get keys
        @do
        def spy_state(effect, k):
            from doeff.program import Resume, Pass
            if isinstance(effect, Get):
                observed_keys.append(effect.key)
                result = yield Resume(k, None)
                return result
            if isinstance(effect, Put):
                result = yield Resume(k, None)
                return result
            yield Pass(effect, k)

        code = """
(require doeff-hy.macros [defp <-])
(require doeff-hy.handle [defhandler])
(import doeff [do :as _doeff-do EffectBase WithHandler run :as doeff-run])
(import doeff_core_effects [Ask Get Put])
(import doeff_core_effects.handlers [lazy-ask])
(import doeff_core_effects.scheduler [scheduled])
(import dataclasses [dataclass])

(defclass [(dataclass :frozen True)] Ping2 [EffectBase])

(defhandler ping-handler2
  (lazy val "cached-value")
  (Ping2 [] (resume val)))

(defp body {:post [(: % object)]}
  (<- r (Ping2))
  r)
"""
        mod = types.ModuleType("my_test_module")
        mod.__file__ = "<test>"
        import sys
        sys.modules["my_test_module"] = mod
        try:
            hy.eval(hy.read_many(code), module=mod)

            prog = mod.body
            wrapped = WithHandler(spy_state,
                        WithHandler(mod.ping_handler2, prog))
            wrapped = scheduled(wrapped)
            doeff_run(wrapped)

            # At least one Get key should contain module name + handler name
            matching = [k for k in observed_keys if "/ping-handler2/val" in k]
            assert len(matching) > 0, (
                f"Expected key containing '/ping-handler2/val', got: {observed_keys}"
            )
        finally:
            del sys.modules["my_test_module"]


# ═══════════════════════════════════════════════════════════════════
# 3. lazy clause in defk
# ═══════════════════════════════════════════════════════════════════

class TestLazyDefk:
    """Test lazy clause in defk."""

    def test_defk_lazy_basic(self):
        """(defk name [x] (lazy val ...) body) should compile and run."""
        import hy
        import doeff_hy  # noqa: F401
        import types

        code = """
(require doeff-hy.macros [defk defp <-])
(import doeff [do :as _doeff-do EffectBase WithHandler run :as doeff-run])
(import doeff_core_effects [Ask Get Put state])
(import doeff_core_effects.handlers [lazy-ask])
(import doeff_core_effects.scheduler [scheduled])

(defk add-prefix [text]
  {:pre [(: text str)] :post [(: % str)]}
  (lazy pfx
    (<- p (Ask "prefix"))
    (+ p ":"))
  (+ pfx text))

(defp body {:post [(: % list)]}
  (<- r1 (add-prefix "hello"))
  (<- r2 (add-prefix "world"))
  [r1 r2])

(setv wrapped body)
(for [h (reversed [(lazy-ask :env {"prefix" "X"}) (state)])]
  (setv wrapped (WithHandler h wrapped)))
(setv wrapped (scheduled wrapped))
(setv __test_result__ (doeff-run wrapped))
"""
        mod = types.ModuleType("_test_lazy_defk")
        mod.__file__ = "<test>"
        import sys
        sys.modules["_test_lazy_defk"] = mod
        try:
            hy.eval(hy.read_many(code), module=mod)
            assert mod.__test_result__ == ["X:hello", "X:world"]
        finally:
            del sys.modules["_test_lazy_defk"]


# ═══════════════════════════════════════════════════════════════════
# 4. <-> effectful threading macro
# ═══════════════════════════════════════════════════════════════════

class TestThreadingMacro:
    """Test <-> effectful threading macro."""

    def test_threading_expands_correctly(self):
        """(<-> (f) (g :k v) (h)) should thread first-arg through pipeline."""
        import hy
        import doeff_hy  # noqa: F401
        import types

        code = """
(require doeff-hy.macros [defk defp <- <->])
(import doeff [do :as _doeff-do EffectBase WithHandler run :as doeff-run])
(import doeff_core_effects [Ask])
(import doeff_core_effects.handlers [lazy-ask])
(import doeff_core_effects.scheduler [scheduled])

(defk step-a [x]
  {:pre [(: x str)] :post [(: % dict)]}
  {"data" x})

(defk step-b [data label]
  {:pre [(: data dict) (: label str)] :post [(: % dict)]}
  (setv result (dict data))
  (setv (get result "label") label)
  result)

(defk step-c [data]
  {:pre [(: data dict)] :post [(: % str)]}
  (str data))

(defk pipeline []
  {:pre [] :post [(: % str)]}
  (<-> (step-a "input")
       (step-b :label "tagged")
       (step-c)))

(setv wrapped (scheduled (pipeline)))
(setv __test_result__ (doeff-run wrapped))
"""
        mod = types.ModuleType("_test_threading")
        mod.__file__ = "<test>"
        import sys
        sys.modules["_test_threading"] = mod
        try:
            hy.eval(hy.read_many(code), module=mod)
            result = mod.__test_result__
            assert isinstance(result, str)
            assert "input" in result
            assert "tagged" in result
        finally:
            del sys.modules["_test_threading"]

    def test_threading_single_form(self):
        """(<-> (f x)) with one form is just (<- _t0 (f x)), _t0."""
        import hy
        import doeff_hy  # noqa: F401
        import types

        code = """
(require doeff-hy.macros [defk defp <- <->])
(import doeff [do :as _doeff-do EffectBase run :as doeff-run])
(import doeff_core_effects.scheduler [scheduled])

(defk identity-k [x]
  {:pre [(: x int)] :post [(: % int)]}
  x)

(defk pipeline [v]
  {:pre [(: v int)] :post [(: % int)]}
  (<-> (identity-k v)))

(setv __test_result__ (doeff-run (scheduled (pipeline 42))))
"""
        mod = types.ModuleType("_test_threading_single")
        mod.__file__ = "<test>"
        import sys
        sys.modules["_test_threading_single"] = mod
        try:
            hy.eval(hy.read_many(code), module=mod)
            assert mod.__test_result__ == 42
        finally:
            del sys.modules["_test_threading_single"]

    def test_threading_two_forms(self):
        """(<-> (f) (g)) threads result of f as first arg to g."""
        import hy
        import doeff_hy  # noqa: F401
        import types

        code = """
(require doeff-hy.macros [defk defp <- <->])
(import doeff [do :as _doeff-do EffectBase run :as doeff-run])
(import doeff_core_effects.scheduler [scheduled])

(defk make-list [x]
  {:pre [(: x int)] :post [(: % list)]}
  [x])

(defk append-item [lst]
  {:pre [(: lst list)] :post [(: % list)]}
  (+ lst [99]))

(defk pipeline [v]
  {:pre [(: v int)] :post [(: % list)]}
  (<-> (make-list v)
       (append-item)))

(setv __test_result__ (doeff-run (scheduled (pipeline 1))))
"""
        mod = types.ModuleType("_test_threading_two")
        mod.__file__ = "<test>"
        import sys
        sys.modules["_test_threading_two"] = mod
        try:
            hy.eval(hy.read_many(code), module=mod)
            assert mod.__test_result__ == [1, 99]
        finally:
            del sys.modules["_test_threading_two"]


# ═══════════════════════════════════════════════════════════════════
# 5. lazy-val / lazy-var + set!
# ═══════════════════════════════════════════════════════════════════

def _hy_eval_in_module(code, mod_name):
    """Helper: eval Hy code in a fresh module, return the module."""
    import hy
    import doeff_hy  # noqa: F401
    import types
    import sys

    mod = types.ModuleType(mod_name)
    mod.__file__ = "<test>"
    sys.modules[mod_name] = mod
    hy.eval(hy.read_many(code), module=mod)
    return mod


class TestLazyValVar:
    """Test lazy-val (immutable) and lazy-var (mutable via set!)."""

    def test_lazy_val_is_alias_for_lazy(self):
        """(lazy-val name body) should work identically to (lazy name body)."""
        import sys
        code = """
(require doeff-hy.macros [defp <-])
(require doeff-hy.handle [defhandler])
(import doeff [do :as _doeff-do EffectBase WithHandler run :as doeff-run])
(import doeff_core_effects [Ask Get Put state])
(import doeff_core_effects.handlers [lazy-ask])
(import doeff_core_effects.scheduler [scheduled])
(import dataclasses [dataclass])

(defclass [(dataclass :frozen True)] Ping [EffectBase])

(defhandler ping-handler
  (lazy-val greeting "hello")
  (Ping [] (resume greeting)))

(setv wrapped (WithHandler ping-handler
                (WithHandler (state) body)))
"""
        # Build body separately to avoid defp issues
        code2 = """
(require doeff-hy.macros [defp <-])
(require doeff-hy.handle [defhandler])
(import doeff [do :as _doeff-do EffectBase WithHandler run :as doeff-run])
(import doeff_core_effects [Get Put state])
(import doeff_core_effects.scheduler [scheduled])
(import dataclasses [dataclass])

(defclass [(dataclass :frozen True)] Ping3 [EffectBase])

(defhandler val-handler
  (lazy-val msg "hi")
  (Ping3 [] (resume msg)))

(defp body {:post [(: % str)]}
  (<- r (Ping3))
  r)

(setv wrapped body)
(setv wrapped (WithHandler val-handler wrapped))
(setv wrapped (WithHandler (state) wrapped))
(setv wrapped (scheduled wrapped))
(setv __test_result__ (doeff-run wrapped))
"""
        mod = _hy_eval_in_module(code2, "_test_lazy_val")
        try:
            assert mod.__test_result__ == "hi"
        finally:
            del sys.modules["_test_lazy_val"]

    def test_lazy_var_with_set_bang(self):
        """lazy-var + set! should update both local and state."""
        import sys
        code = """
(require doeff-hy.macros [defk defp <- set!])
(require doeff-hy.handle [defhandler])
(import doeff [do :as _doeff-do EffectBase WithHandler run :as doeff-run])
(import doeff_core_effects [Get Put state])
(import doeff_core_effects.scheduler [scheduled])
(import dataclasses [dataclass])

(defclass [(dataclass :frozen True)] GetCounter [EffectBase])
(defclass [(dataclass :frozen True)] Increment [EffectBase])

(defhandler counter-handler
  (lazy-var count 0)
  (GetCounter [] (resume count))
  (Increment []
    (set! count (+ count 1))
    (resume count)))

(defp body {:post [(: % list)]}
  (<- c0 (GetCounter))
  (<- c1 (Increment))
  (<- c2 (Increment))
  (<- c3 (GetCounter))
  [c0 c1 c2 c3])

(setv wrapped body)
(setv wrapped (WithHandler counter-handler wrapped))
(setv wrapped (WithHandler (state) wrapped))
(setv wrapped (scheduled wrapped))
(setv __test_result__ (doeff-run wrapped))
"""
        mod = _hy_eval_in_module(code, "_test_lazy_var")
        try:
            assert mod.__test_result__ == [0, 1, 2, 2]
        finally:
            del sys.modules["_test_lazy_var"]

    def test_lazy_var_set_bang_persists_across_calls(self):
        """set! on lazy-var should persist via state (not just local)."""
        import sys
        code = """
(require doeff-hy.macros [defk defp <- set!])
(require doeff-hy.handle [defhandler])
(import doeff [do :as _doeff-do EffectBase WithHandler run :as doeff-run Some])
(import doeff_core_effects [Get Put state])
(import doeff_core_effects.scheduler [scheduled])
(import dataclasses [dataclass])

(defclass [(dataclass :frozen True)] AddItem [EffectBase]
  #^ str item)
(defclass [(dataclass :frozen True)] GetItems [EffectBase])

(defhandler bag-handler
  (lazy-var items [])
  (AddItem [item]
    (set! items (+ items [item]))
    (resume None))
  (GetItems []
    (resume items)))

(defp body {:post [(: % list)]}
  (<- (AddItem :item "a"))
  (<- (AddItem :item "b"))
  (<- result (GetItems))
  result)

(setv wrapped body)
(setv wrapped (WithHandler bag-handler wrapped))
(setv wrapped (WithHandler (state) wrapped))
(setv wrapped (scheduled wrapped))
(setv __test_result__ (doeff-run wrapped))
"""
        mod = _hy_eval_in_module(code, "_test_lazy_var_persist")
        try:
            assert mod.__test_result__ == ["a", "b"]
        finally:
            del sys.modules["_test_lazy_var_persist"]

    def test_lazy_val_set_bang_compile_error(self):
        """set! on lazy-val should raise SyntaxError at compile time."""
        import hy
        import doeff_hy  # noqa: F401

        code = """
(require doeff-hy.macros [set!])
(require doeff-hy.handle [defhandler])
(import doeff [EffectBase])
(import dataclasses [dataclass])

(defclass [(dataclass :frozen True)] Ping [EffectBase])

(defhandler bad-handler
  (lazy-val x 0)
  (Ping []
    (set! x 1)
    (resume x)))
"""
        with pytest.raises(Exception, match="lazy-val.*immutable|set!.*lazy-val"):
            hy.eval(hy.read_many(code))

    def test_lazy_var_in_defk(self):
        """lazy-var + set! should work in defk context too."""
        import sys
        code = """
(require doeff-hy.macros [defk defp <- set!])
(import doeff [do :as _doeff-do EffectBase WithHandler run :as doeff-run])
(import doeff_core_effects [Get Put state])
(import doeff_core_effects.scheduler [scheduled])

(defk accumulate [item]
  {:pre [(: item str)] :post [(: % list)]}
  (lazy-var items [])
  (set! items (+ items [item]))
  items)

(defp body {:post [(: % list)]}
  (<- r1 (accumulate "a"))
  (<- r2 (accumulate "b"))
  [r1 r2])

(setv wrapped body)
(setv wrapped (WithHandler (state) wrapped))
(setv wrapped (scheduled wrapped))
(setv __test_result__ (doeff-run wrapped))
"""
        mod = _hy_eval_in_module(code, "_test_lazy_var_defk")
        try:
            assert mod.__test_result__ == [["a"], ["a", "b"]]
        finally:
            del sys.modules["_test_lazy_var_defk"]
