"""Tests for defhandler macro — <- (bind) and ! (bang) support.

Verifies:
  - defhandler clause body can use <- to delegate effects to outer handler
  - defhandler clause body can use ! (bang) for inline delegation
  - handle (inline) also supports <- and !
"""

import importlib
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
