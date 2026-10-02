"""Tests for macro self-containment — no external _doeff-do import needed.

Every doeff-hy macro should inject its own runtime imports so users
only need (require doeff-hy.macros [...]) without manual
(import doeff [do :as _doeff-do]).
"""

import sys
import types
from dataclasses import dataclass

import doeff_hy  # noqa — registers extensions
import hy
import hy.macros
import pytest
from doeff_core_effects import Ask
from doeff_core_effects.handlers import await_handler, lazy_ask
from doeff_core_effects.scheduler import scheduled

from doeff import EffectBase, Expand, Pure, run


@dataclass(frozen=True)
class Num(EffectBase):
    value: int


def _eval_no_doeff_do(code: str, **extra_globals):
    """Evaluate Hy code WITHOUT _doeff-do in scope.

    Only provides: require for macros, user-level types, run.
    Does NOT provide: _doeff-do, do, _doeff_do.
    """
    module_name = "test_self_contained"
    mod = types.ModuleType(module_name)
    sys.modules[module_name] = mod

    mod.__dict__.update({
        "run": run,
        "scheduled": scheduled,
        "await_handler": await_handler,
        "lazy_ask": lazy_ask,
        "Ask": Ask,
        "Num": Num,
        **extra_globals,
    })

    # Require ALL macros — but do NOT inject _doeff-do
    hy.macros.require("doeff_hy.macros", mod, assignments=[
        ["defk", "defk"],
        ["deff", "deff"],
        ["fnk", "fnk"],
        ["do!", "do!"],
        ["defp", "defp"],
        ["defpp", "defpp"],
        ["<-", "<-"],
        ["deftest", "deftest"],
        ["defhandler", "defhandler"],
    ])

    tree = hy.read_many(code)
    result = None
    for form in tree:
        result = hy.eval(form, mod.__dict__, module=mod)
    return result


class TestDefkSelfContained:
    def test_defk_no_external_import(self):
        """defk should work without (import doeff [do :as _doeff-do])."""
        result = _eval_no_doeff_do("""
        (defk add-one [x]
          {:pre [(: x int)]
           :post [(: % int)]}
          (+ x 1))
        (run (add-one 5))
        """)
        assert result == 6

    def test_defk_rejects_bare_effect_last_expression(self):
        """defk should fail fast when the final expression is an unperformed effect."""
        with pytest.raises(RuntimeError, match="last expression is an unperformed effect"):
            _eval_no_doeff_do("""
            (defk bad [x]
              {:pre [(: x int)]
               :post [(: % int)]}
              (Num :value x))
            (run (bad 5))
            """)


class TestFnkSelfContained:
    def test_fnk_no_external_import(self):
        """fnk should work without (import doeff [do :as _doeff-do])."""
        result = _eval_no_doeff_do("""
        (setv k (fnk [x] (* x 2)))
        (run (k 5))
        """)
        assert result == 10


class TestDoBangSelfContained:
    def test_do_bang_no_external_import(self):
        """do! should work without (import doeff [do :as _doeff-do])."""
        result = _eval_no_doeff_do("""
        (run (scheduled
          ((await_handler) ((lazy_ask :env {"key" "hello"})
              (do!
                (<- val (Ask "key"))
                (+ val " world"))) )))
        """)
        assert result == "hello world"

    def test_do_bang_rejects_bare_effect_last_expression(self):
        """do! should fail fast when the final expression is an unperformed effect."""
        with pytest.raises(RuntimeError, match="last expression is an unperformed effect"):
            _eval_no_doeff_do("""
            (run (do!
              (Num :value 5)))
            """)


class TestDefpSelfContained:
    def test_defp_no_external_import(self):
        """defp should work without (import doeff [do :as _doeff-do])."""
        result = _eval_no_doeff_do("""
        (defp my-prog
          {:post [(: % str)]}
          (<- val (Ask "key"))
          (+ val " world"))

        (run (scheduled
          ((await_handler) ((lazy_ask :env {"key" "hello"})
              my-prog) )))
        """)
        assert result == "hello world"


class TestDeftestSelfContained:
    def test_deftest_no_external_import(self):
        """deftest should work without (import doeff [do :as _doeff-do]).

        deftest expands to a pytest function. We verify the expansion
        doesn't fail due to missing _doeff-do.
        """
        _eval_no_doeff_do("""
        (deftest test-self-contained
          (<- val (Ask "key"))
          (assert (= val "hello")))
        """)


def test_runtime_guards_do_not_import_per_call() -> None:
    """The guards a defk body calls on every call hold their names from the module (#844).

    The slow shape: `_guard-performed` ran `(import doeff [EffectBase])` inside the function,
    so every defk call went through the import machinery (`_handle_fromlist`, ~0.5 µs).
    """
    import dis

    from doeff_hy.macros import _guard_performed, _guard_statement_value

    for guard in (_guard_performed, _guard_statement_value):
        opnames = {instruction.opname for instruction in dis.get_instructions(guard)}
        assert "IMPORT_NAME" not in opnames, guard.__name__


@pytest.mark.parametrize("source", [
    "(defk good [] {:pre [] :post [(: % int)]} 7) (good)",
    "(defk good [] {:pre [] :post [(: % int)]} (<- x (do! 7)) x) (good)",
    "(do! 7)",
    "(do! {:post [(: % int)]} 7)",
    "(defclass Box [] (defk good [self] {:pre [(: self Box)] :post [(: % int)]} 7)) (.good (Box))",
])
def test_value_returns_skip_the_error_guard(source: str) -> None:
    """Successful returns must not pay for the error-reporting Python call (#2817)."""
    program = _eval_no_doeff_do(source)
    assert isinstance(program, Expand)
    module = sys.modules["test_self_contained"]
    calls: list[str] = []
    original = module._guard_performed

    def observe(frame: types.FrameType, event: str, arg: object) -> None:
        if event == "call" and frame.f_code is original.__code__:
            calls.append(frame.f_locals["label"])

    previous = sys.getprofile()
    sys.setprofile(observe)
    try:
        assert run(program) == 7
    finally:
        sys.setprofile(previous)
    assert calls == []


@pytest.mark.parametrize("source", [
    "(defk bad [] {:pre [] :post [(: % int)]} (Num 7)) (bad)",
    "(defk bad [] {:pre [] :post [(: % int)]} (<- x (do! 7)) (Num x)) (bad)",
    "(do! (Num 7))",
    "(do! {:post [(: % int)]} (Num 7))",
    "(defclass Box [] (defk bad [self] {:pre [(: self Box)] :post [(: % int)]} (Num 7))) (.bad (Box))",
])
def test_error_guard_still_precedes_the_return_contract(source: str) -> None:
    """A bare effect is rejected, including in generators and class bodies."""
    program = _eval_no_doeff_do(source)
    assert isinstance(program, Expand)
    with pytest.raises(RuntimeError, match="last expression is an unperformed effect `Num`"):
        run(program)


@pytest.mark.parametrize("program_kind", ["pure", "expand"])
@pytest.mark.parametrize("source", [
    "(defk kept [] {:pre [] :post [(: % (| Pure Expand))]} payload) (kept)",
    "(defk kept [] {:pre [] :post [(: % (| Pure Expand))]} (<- x (do! 7)) payload) (kept)",
    "(do! payload)",
    "(do! {:post [(: % (| Pure Expand))]} payload)",
    (
        "(defclass Box [] (defk kept [self] {:pre [(: self Box)] "
        ":post [(: % (| Pure Expand))]} payload)) (.kept (Box))"
    ),
])
def test_program_return_preserves_identity(source: str, program_kind: str) -> None:
    """A Program is a return value here, not an unperformed Effect or an implicit bind."""
    # This Expand raises if run: returning it must neither execute it nor wrap/copy it.
    payload = Pure(7) if program_kind == "pure" else _eval_no_doeff_do("(do! (Num 7))")
    assert isinstance(payload, (Pure, Expand))
    program = _eval_no_doeff_do(source, payload=payload, Pure=Pure, Expand=Expand)
    assert isinstance(program, Expand)
    assert run(program) is payload
