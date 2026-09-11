"""Typed effect binding — (<- name Type expr) carries the same isinstance
guarantee wherever it is written.

The `<-` macro alone emitted `(assert (isinstance name Type) …)` for the
4-element form, but the body expanders that pre-parse `<-` forms (deftest /
do! / defp / for/do) went through `_bind-parts`, which dropped the type. A
typed bind at the top level of those bodies was silently untyped at runtime,
so the shared quality checker (dotfiles agent/quality/hy_dsl.py effect_bind)
had to project it as `object` — 30+ pyright findings on ACP stage-0 laws.

Pins the single definition point `_bind-yield` in doeff_hy/macros.hy:
  - expansion shape: the 4-element form yields an isinstance assert in every
    expander; the 2/3-element forms do not (unchanged)
  - runtime (do! / defp): matching type passes, mismatch raises AssertionError
    naming the expected and the actual type
The deftest runtime cases live next to the other deftest tests
(tests/test_deftest_macro.py::TestDeftestTypedBind).
"""

import sys
import types

import doeff_hy  # noqa: F401 — registers extensions
import hy
import hy.macros
import pytest
from doeff_core_effects import Ask
from doeff_core_effects.handlers import await_handler, lazy_ask
from doeff_core_effects.scheduler import scheduled

from doeff import run

_MACROS = ["<-", "do!", "defp", "deftest", "for/do", "traverse"]


def _macro_module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    mod.__dict__.update({
        "run": run, "scheduled": scheduled, "await_handler": await_handler,
        "lazy_ask": lazy_ask, "Ask": Ask,
    })
    hy.macros.require("doeff_hy.macros", mod, assignments=[[m, m] for m in _MACROS])
    return mod


def _expand(code: str) -> str:
    """Top-level macro expansion as text (nested macros stay unexpanded)."""
    mod = _macro_module("typed_bind_expand")
    return hy.repr(hy.macroexpand(hy.read(code), module=mod))


def _eval(code: str):
    mod = _macro_module("typed_bind_eval")
    result = None
    for form in hy.read_many(code):
        result = hy.eval(form, mod.__dict__, module=mod)
    return result


# ---------------------------------------------------------------------------
# Expansion shape — one definition point, every expander
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code", [
    '(<- x str (Eff))',
    '(do! (<- x str (Eff)) x)',
    '(defp p {:post [(: % str)]} (<- x str (Eff)) x)',
    '(deftest test-x (<- x str (Eff)) (assert x))',
    '(for/do (<- item (From [1 2])) (<- x str (Eff)) x)',
    '(traverse (<- item (Iterate [1 2])) (<- x str (Eff)) x)',
], ids=["<-", "do!", "defp", "deftest", "for/do", "traverse"])
def test_four_element_bind_emits_isinstance_everywhere(code):
    expanded = _expand(code)
    assert "(setv x (yield (Eff)))" in expanded, expanded
    assert "(assert (isinstance x str)" in expanded, expanded


@pytest.mark.parametrize("code", [
    '(<- x (Eff))',
    '(do! (<- x (Eff)) (<- (Eff)) x)',
    '(defp p {:post [(: % str)]} (<- x (Eff)) (<- (Eff)) x)',
    '(deftest test-x (<- x (Eff)) (<- (Eff)) (assert x))',
    '(for/do (<- item (From [1 2])) (<- x (Eff)) (<- (Eff)) x)',
], ids=["<-", "do!", "defp", "deftest", "for/do"])
def test_two_and_three_element_binds_stay_unchecked(code):
    expanded = _expand(code)
    assert "(yield (Eff))" in expanded, expanded
    # :post の (: % str) は isinstance を出すので、束縛名 x の検査だけを見る
    assert "(isinstance x" not in expanded, expanded


# ---------------------------------------------------------------------------
# Runtime — do! / defp
# ---------------------------------------------------------------------------

def _run_do_bang(type_name: str):
    return _eval(f"""
    (run (scheduled
      ((await_handler) ((lazy_ask :env {{"key" "hello"}})
          (do!
            (<- val {type_name} (Ask "key"))
            (+ val " world"))))))
    """)


def _run_defp(type_name: str):
    return _eval(f"""
    (defp typed-prog
      {{:post [(: % str)]}}
      (<- val {type_name} (Ask "key"))
      (+ val " world"))
    (run (scheduled
      ((await_handler) ((lazy_ask :env {{"key" "hello"}})
          typed-prog))))
    """)


@pytest.mark.parametrize("runner", [_run_do_bang, _run_defp], ids=["do!", "defp"])
def test_typed_bind_passes_when_type_matches(runner):
    assert runner("str") == "hello world"


@pytest.mark.parametrize("runner", [_run_do_bang, _run_defp], ids=["do!", "defp"])
def test_typed_bind_fails_when_type_mismatches(runner):
    with pytest.raises(AssertionError, match=r"expected int, got str"):
        runner("int")
