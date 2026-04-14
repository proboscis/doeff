"""Tests for defhandler macro — <- (bind) and ! (bang) support.

Verifies:
  - defhandler clause body can use <- to delegate effects to outer handler
  - defhandler clause body can use ! (bang) for inline delegation
  - handle (inline) also supports <- and !
  - defhandler inside defn factory does not leak yield (#387)
"""

import importlib
import inspect
import sys
import textwrap

import pytest
import hy
import hy.compiler
import hy.reader
import hy.macros

import doeff_hy  # noqa — registers extensions
from doeff import DoExpr, WithHandler, Resume, Transfer, Pass, run, do as _doeff_do
from doeff import EffectBase
from doeff_core_effects.scheduler import scheduled
from doeff_core_effects.handlers import await_handler
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Test effects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Add(EffectBase):
    x: int
    y: int


@dataclass(frozen=True)
class Store(EffectBase):
    value: object


@_doeff_do
def add_program():
    result = yield Add(x=3, y=4)
    yield Store(value=result)
    return result


def real_add_handler():
    @_doeff_do
    def _handler(effect, k):
        if isinstance(effect, Add):
            yield Resume(k, effect.x + effect.y)
        else:
            yield Pass(effect, k)
    return _handler


def store_handler(results_list):
    @_doeff_do
    def _handler(effect, k):
        if isinstance(effect, Store):
            results_list.append(effect.value)
            yield Resume(k, None)
        else:
            yield Pass(effect, k)
    return _handler


# ---------------------------------------------------------------------------
# Eval helper
# ---------------------------------------------------------------------------

def _eval_hy(code: str, **extra_globals):
    """Evaluate Hy code with handle macros and test effects."""
    import types
    module_name = "test_handle_eval"
    mod = types.ModuleType(module_name)
    sys.modules[module_name] = mod

    mod.__dict__.update({
        "run": run,
        "do": _doeff_do,
        "_doeff_do": _doeff_do,
        "WithHandler": WithHandler,
        "Resume": Resume,
        "Transfer": Transfer,
        "Pass": Pass,
        "Add": Add,
        "Store": Store,
        "add_program": add_program,
        "real_add_handler": real_add_handler,
        "store_handler": store_handler,
        "scheduled": scheduled,
        "await_handler": await_handler,
        **extra_globals,
    })

    hy.macros.require("doeff_hy.handle", mod, assignments=[
        ["handle", "handle"],
        ["defhandler", "defhandler"],
    ])

    tree = hy.read_many(code)
    result = None
    for form in tree:
        result = hy.eval(form, mod.__dict__, module=mod)
    return result


# ---------------------------------------------------------------------------
# Tests — defhandler with <- and !
# ---------------------------------------------------------------------------

class TestDefhandlerBind:
    """Test <- and ! inside defhandler clauses."""

    def test_bind_delegates_to_outer(self):
        """(<- result effect) in handler delegates to outer handler."""
        results = []
        _eval_hy("""
        (do
          (defhandler doubling-handler
            (Add [x y]
              (<- single (Add :x x :y y))
              (resume (* single 2))))

          (run (scheduled
            (WithHandler (await_handler)
              (WithHandler (store_handler results)
                (WithHandler (real_add_handler)
                  (WithHandler doubling-handler (add_program))))))))
        """, results=results)
        # Add(3,4) → doubling delegates → real_add gets 7 → doubles to 14 → Store(14)
        assert results == [14]

    def test_bang_in_resume(self):
        """! (bang) in handler clause expands to bind."""
        results = []
        _eval_hy("""
        (do
          (defhandler split-handler
            (Add [x y]
              (resume (+ (! (Add :x x :y 0))
                         (! (Add :x 0 :y y))))))

          (run (scheduled
            (WithHandler (await_handler)
              (WithHandler (store_handler results)
                (WithHandler (real_add_handler)
                  (WithHandler split-handler (add_program))))))))
        """, results=results)
        # Add(3,4) → split: Add(3,0)+Add(0,4) = 3+4 = 7 → Store(7)
        assert results == [7]

    def test_bind_without_name(self):
        """(<- expr) without name — side-effect delegation."""
        results = []
        _eval_hy("""
        (do
          (defhandler logging-handler
            (Add [x y]
              (<- (Add :x 0 :y 0))
              (resume (+ x y))))

          (run (scheduled
            (WithHandler (await_handler)
              (WithHandler (store_handler results)
                (WithHandler (real_add_handler)
                  (WithHandler logging-handler (add_program))))))))
        """, results=results)
        # delegates Add(0,0) as side-effect, resumes with x+y=7 → Store(7)
        assert results == [7]


class TestInlineHandleBind:
    """Test <- and ! inside inline handle macro."""

    def test_handle_with_bind(self):
        """(<- result effect) in inline handle."""
        results = []
        _eval_hy("""
        (run (scheduled
          (WithHandler (await_handler)
            (WithHandler (store_handler results)
              (WithHandler (real_add_handler)
                (handle (add_program)
                  (Add [x y]
                    (<- single (Add :x x :y y))
                    (resume (* single 3)))))))))
        """, results=results)
        # Add(3,4) → delegate → 7 → triple → 21 → Store(21)
        assert results == [21]

    def test_handle_with_bang(self):
        """! (bang) in inline handle."""
        results = []
        _eval_hy("""
        (run (scheduled
          (WithHandler (await_handler)
            (WithHandler (store_handler results)
              (WithHandler (real_add_handler)
                (handle (add_program)
                  (Add [x y]
                    (resume (- (! (Add :x x :y y)) 1)))))))))
        """, results=results)
        # Add(3,4) → delegate → 7 → subtract 1 → 6 → Store(6)
        assert results == [6]


# ---------------------------------------------------------------------------
# Minimal eval helper — no doeff internals pre-injected
# ---------------------------------------------------------------------------

def _eval_hy_minimal(code: str, **extra_globals):
    """Evaluate Hy code WITHOUT pre-injecting doeff internals.

    Only provides user-level imports (effect types, run, scheduled).
    Does NOT provide: _doeff_do, Resume, Transfer, Pass, WithHandler.
    """
    import types
    module_name = "test_handle_self_contained"
    mod = types.ModuleType(module_name)
    sys.modules[module_name] = mod

    mod.__dict__.update({
        "run": run,
        "Add": Add,
        "Store": Store,
        "add_program": add_program,
        "store_handler": store_handler,
        "real_add_handler": real_add_handler,
        "scheduled": scheduled,
        "await_handler": await_handler,
        **extra_globals,
    })

    hy.macros.require("doeff_hy.handle", mod, assignments=[
        ["handle", "handle"],
        ["defhandler", "defhandler"],
    ])

    tree = hy.read_many(code)
    result = None
    for form in tree:
        result = hy.eval(form, mod.__dict__, module=mod)
    return result


# ---------------------------------------------------------------------------
# Tests — self-contained macros (no manual doeff imports needed)
# ---------------------------------------------------------------------------

class TestSelfContainedMacros:
    """defhandler and handle must inject their own runtime deps."""

    def test_defhandler_no_extra_imports(self):
        """defhandler should define a handler without user importing doeff internals."""
        _eval_hy_minimal("""
        (defhandler simple-add
          (Add [x y] (resume (+ x y))))
        """)

    def test_defhandler_parameterized_no_extra_imports(self):
        """Parameterized defhandler should also be self-contained."""
        _eval_hy_minimal("""
        (defhandler scaled-add [scale]
          (Add [x y] (resume (* (+ x y) scale))))
        """)

    def test_defhandler_end_to_end(self):
        """defhandler + run end-to-end — user only imports WithHandler for composition."""
        results = []
        _eval_hy_minimal("""
        (import doeff [WithHandler])
        (defhandler simple-add
          (Add [x y] (resume (+ x y))))

        (run (scheduled
          (WithHandler (await_handler)
            (WithHandler (store_handler results)
              (WithHandler simple-add (add_program))))))
        """, results=results)
        assert results == [7]

    def test_defhandler_with_transfer(self):
        """transfer in defhandler should also work without extra imports."""
        results = []
        _eval_hy_minimal("""
        (import doeff [WithHandler])
        (defhandler add-and-transfer
          (Add [x y] (transfer (+ x y))))

        (run (scheduled
          (WithHandler (await_handler)
            (WithHandler (store_handler results)
              (WithHandler add-and-transfer (add_program))))))
        """, results=results)
        # transfer removes add-and-transfer; Store still hits store_handler
        assert results == [7]

    def test_defhandler_with_pass(self):
        """pass in defhandler (via unmatched default) works without extra imports.

        noop-handler matches no effects used by add_program, so everything
        auto-passes through it to the real handlers above.
        """
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class Unused(EffectBase):
            pass

        results = []
        _eval_hy_minimal("""
        (import doeff [WithHandler])
        ;; noop-handler only matches Unused — Add and Store pass through
        (defhandler noop-handler
          (Unused [] (resume None)))

        (run (scheduled
          (WithHandler (await_handler)
            (WithHandler (store_handler results)
              (WithHandler (real_add_handler)
                (WithHandler noop-handler (add_program)))))))
        """, results=results, Unused=Unused)
        assert results == [7]

    def test_handle_inline_no_extra_imports(self):
        """handle (inline) should also inject its own deps."""
        results = []
        _eval_hy_minimal("""
        (run (scheduled
          (WithHandler (await_handler)
            (WithHandler (store_handler results)
              (handle (add_program)
                (Add [x y] (resume (+ x y))))))))
        """, results=results)
        assert results == [7]


# ---------------------------------------------------------------------------
# Eval helper — both <- macro and defhandler macro (simulates real file)
# ---------------------------------------------------------------------------

def _eval_hy_with_bind(code: str, **extra_globals):
    """Evaluate Hy code with BOTH <- macro (from macros) AND defhandler (from handle).

    This simulates the real-world pattern:
        (require doeff-hy.macros [defk <-])
        (require doeff-hy.handle [defhandler])
    """
    import types
    module_name = "test_handle_factory"
    mod = types.ModuleType(module_name)
    sys.modules[module_name] = mod

    mod.__dict__.update({
        "run": run,
        "do": _doeff_do,
        "_doeff_do": _doeff_do,
        "WithHandler": WithHandler,
        "Resume": Resume,
        "Transfer": Transfer,
        "Pass": Pass,
        "Add": Add,
        "Store": Store,
        "add_program": add_program,
        "real_add_handler": real_add_handler,
        "store_handler": store_handler,
        "scheduled": scheduled,
        "await_handler": await_handler,
        **extra_globals,
    })

    # Require both <- from macros AND defhandler from handle
    hy.macros.require("doeff_hy.macros", mod, assignments=[
        ["<-", "<-"],
    ])
    hy.macros.require("doeff_hy.handle", mod, assignments=[
        ["handle", "handle"],
        ["defhandler", "defhandler"],
    ])

    tree = hy.read_many(code)
    result = None
    for form in tree:
        result = hy.eval(form, mod.__dict__, module=mod)
    return result


# ---------------------------------------------------------------------------
# Tests — defhandler inside defn factory (#387)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Init(EffectBase):
    """Side-effect for initializing state."""
    label: str


class TestDefhandlerFactoryYieldLeak:
    """defhandler inside defn factory must not leak yield to outer defn.

    Regression tests for #387: when <- appears nested inside (when ...)
    in a handler clause body, _expand_handler_binds does not reach it.
    The remaining <- is later expanded by the <- macro into (yield ...),
    which may leak to the outer defn scope.
    """

    def test_factory_returns_function_not_generator_toplevel_bind(self):
        """defhandler with <- at top level inside defn factory."""
        result = _eval_hy_with_bind("""
        (defn make-handler [config]
          (defhandler _handler
            (Add [x y]
              (<- delegated (Add :x x :y y))
              (resume (* delegated 2))))
          _handler)

        (make-handler "test")
        """)
        assert not inspect.isgenerator(result), \
            f"factory returned generator instead of handler: {type(result)}"

    def test_factory_returns_function_not_generator_nested_bind(self):
        """defhandler with <- nested inside (when ...) — the #387 pattern.

        _expand_handler_binds only handles top-level <- in clause body.
        Nested <- inside (when ...) is left for the <- macro to expand,
        generating (yield ...) that could leak to the outer defn.
        """
        result = _eval_hy_with_bind("""
        (defn make-handler [base-url]
          (setv _state {"client" None})
          (defhandler _handler
            (Add [x y]
              (when (is (get _state "client") None)
                (<- init-result (Init :label "setup"))
                (setv (get _state "client") "ready"))
              (resume (+ x y))))
          _handler)

        (make-handler "http://test")
        """, Init=Init)
        assert not inspect.isgenerator(result), \
            f"factory returned generator instead of handler: {type(result)}"

    def test_factory_returns_function_multi_clause_nested_bind(self):
        """Multiple clauses each with nested <- — full #387 pattern.

        Simulates handlers.hy: 6 clauses, each with lazy init via
        nested (<- secret ...) inside (when ...).
        """
        result = _eval_hy_with_bind("""
        (defn make-handler [base-url]
          (setv _state {"client" None})
          (defhandler _handler
            (Add [x y]
              (when (is (get _state "client") None)
                (<- _init (Init :label "add-init"))
                (setv (get _state "client") "ready"))
              (<- delegated (Add :x x :y y))
              (resume delegated))
            (Store [value]
              (when (is (get _state "client") None)
                (<- _init (Init :label "store-init"))
                (setv (get _state "client") "ready"))
              (resume None)))
          _handler)

        (make-handler "http://test")
        """, Init=Init)
        assert not inspect.isgenerator(result), \
            f"factory returned generator instead of handler: {type(result)}"

    def test_factory_handler_works_end_to_end(self):
        """Factory-produced handler actually works when composed."""
        results = []
        _eval_hy_with_bind("""
        (defn make-handler [base-url]
          (setv _state {"client" None})
          (defhandler _handler
            (Add [x y]
              (when (is (get _state "client") None)
                (<- _init (Init :label "setup"))
                (setv (get _state "client") "ready"))
              (resume (+ x y))))
          _handler)

        (defn init-handler []
          "Handle Init effects (return label as confirmation)."
          (_doeff-do
            (fn [effect k]
              (if (isinstance effect Init)
                  (yield (Resume k (. effect label)))
                  (yield (Pass effect k))))))

        (setv h (make-handler "http://test"))
        (run (scheduled
          (WithHandler (await_handler)
            (WithHandler (store_handler results)
              (WithHandler (real_add_handler)
                (WithHandler (init-handler)
                  (WithHandler h (add_program))))))))
        """, results=results, Init=Init)
        assert results == [7]


class TestDefhandlerFactoryModuleCompile:
    """Same tests via .hy module import (full Hy compilation path).

    hy.eval processes forms individually; module import compiles the
    entire file. The #387 bug may only surface under module compilation
    because the Hy compiler accumulates state across top-level forms.
    """

    @pytest.fixture(autouse=True)
    def _import_repro(self):
        """Import the repro .hy fixture as a module."""
        # Clear cached module for fresh compile each test
        sys.modules.pop("tests.fixtures.repro_387", None)
        sys.modules.pop("repro_387", None)
        import tests.fixtures.repro_387 as mod
        self.mod = mod

    def test_make_handler_not_generator(self):
        """make-handler factory must return handler, not generator."""
        result = self.mod.make_handler("http://test")
        assert not inspect.isgenerator(result), \
            f"make_handler returned generator: {type(result)}"
        assert callable(result)

    def test_make_handler_many_clauses_not_generator(self):
        """make-handler-many-clauses — multi-clause variant."""
        result = self.mod.make_handler_many_clauses("http://test")
        assert not inspect.isgenerator(result), \
            f"make_handler_many_clauses returned generator: {type(result)}"
        assert callable(result)

    def test_factory_function_not_generator_function(self):
        """The factory defn itself must not be a generator function."""
        assert not inspect.isgeneratorfunction(self.mod.make_handler), \
            "make_handler is a generator function — yield leaked to outer defn"
        assert not inspect.isgeneratorfunction(self.mod.make_handler_many_clauses), \
            "make_handler_many_clauses is a generator function — yield leaked"

    def test_pre_handlers_are_callable(self):
        """Sanity check: _doeff-do handlers before defhandler still work."""
        h1 = self.mod.pre_handler_1()
        h2 = self.mod.pre_handler_2("config")
        h3 = self.mod.pre_handler_3("http://test")
        assert callable(h1)
        assert callable(h2)
        assert callable(h3)


class TestDefhandlerMissingRequire:
    """Repro for #387: defhandler used WITHOUT (require doeff-hy.handle [defhandler]).

    Root cause: when only <- is required but defhandler is NOT required,
    (defhandler ...) is compiled as a plain function call. The <- macro
    inside clauses still expands to (yield ...), but without the defhandler
    macro creating the inner (fn [effect k] ...), the yield lands in the
    outer defn — making the factory a generator function.
    """

    @pytest.fixture(autouse=True)
    def _import_repro(self):
        """Import the no-require repro fixture."""
        import os
        sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
        sys.modules.pop("fixtures.repro_387_no_require", None)
        import fixtures.repro_387_no_require as mod
        self.mod = mod

    def test_yield_leaks_without_defhandler_require(self):
        """Without (require doeff-hy.handle [defhandler]), factory becomes generator.

        This is the actual #387 bug — the fix is to add the require.
        """
        assert inspect.isgeneratorfunction(self.mod.make_handler_missing_require), \
            "Expected yield leak: defhandler without require should make outer defn a generator"


class TestDefhandlerReExport:
    """defhandler available via (require doeff-hy.macros [...]) re-export.

    After the #387 fix, users only need one require line instead of two.
    """

    @pytest.fixture(autouse=True)
    def _import_repro(self):
        import os
        sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
        sys.modules.pop("fixtures.repro_387_via_macros", None)
        import fixtures.repro_387_via_macros as mod
        self.mod = mod

    def test_defhandler_via_macros_require(self):
        """(require doeff-hy.macros [defk <- defhandler]) should work."""
        assert not inspect.isgeneratorfunction(self.mod.make_handler), \
            "defhandler via macros re-export should not leak yield"
        result = self.mod.make_handler("http://test")
        assert callable(result)
        assert not inspect.isgenerator(result)
