"""Tests for macro self-containment — no external _doeff-do import needed.

Every doeff-hy macro should inject its own runtime imports so users
only need (require doeff-hy.macros [...]) without manual
(import doeff [do :as _doeff-do]).
"""

import sys
import types

import pytest
import hy
import hy.macros

import doeff_hy  # noqa — registers extensions
from doeff import run, WithHandler
from doeff import EffectBase
from doeff_core_effects import Ask
from doeff_core_effects.handlers import await_handler, lazy_ask
from doeff_core_effects.scheduler import scheduled
from dataclasses import dataclass


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
        "WithHandler": WithHandler,
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
          (WithHandler (await_handler)
            (WithHandler (lazy_ask :env {"key" "hello"})
              (do!
                (<- val (Ask "key"))
                (+ val " world"))))))
        """)
        assert result == "hello world"


class TestDefpSelfContained:
    def test_defp_no_external_import(self):
        """defp should work without (import doeff [do :as _doeff-do])."""
        result = _eval_no_doeff_do("""
        (defp my-prog
          {:post [(: % str)]}
          (<- val (Ask "key"))
          (+ val " world"))

        (run (scheduled
          (WithHandler (await_handler)
            (WithHandler (lazy_ask :env {"key" "hello"})
              my-prog))))
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
