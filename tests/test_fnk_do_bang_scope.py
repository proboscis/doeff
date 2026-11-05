"""Test that the expansion of fnk / do! creates no names in the scope where it is written, and reads that scope's names as before.

The runtime expansion of fnk / do! used to import helpers such as `_doeff_do`, `_guard_statement_value`, and `_doeff_effect_base` at the place where it was
written. Importing them inside a defk body (a function) made the names local to the whole function, so the traverse / for/do `_doeff_do`
read earlier in the same function, the body-statement guard `_guard_statement_value`, and the final-value guard `_doeff_effect_base` failed with
UnboundLocalError. The plain replacement of fold, `(Reduce (fnk [acc v] ...) init coll)`, lands exactly in that position.

fnk builds the lambda where it is written (default argument values are evaluated in that scope -- names in a class body are also readable) and looks up `do` each time it is called
via `__import__`. do! puts the helper import and the Program construction inside the function that is called immediately. Both work even if the module where they are written
has neither defk nor an import of `_doeff_do` (they do not rely on names defk puts in the module's globals).
"""

import sys
import types

import doeff_hy  # noqa: F401 — registers extensions
import hy
import hy.macros
import pytest
from doeff_core_effects import scheduled, try_handler
from doeff_hy import macros
from doeff_traverse import fail_handler, sequential

from doeff import run


def _helper_names() -> frozenset[str]:
    """Helper names the expansion reads by name: the names bound by the imports of `_helper-imports`, and the outcomes names defk puts in globals.

    Read them from `_helper-imports` rather than copying the table -- so this test also sees new names when a helper is added.
    """
    names: set[str] = {hy.mangle(macros._HELPERS_NAME)}
    for statement in macros._helper_imports()[1:]:
        assert str(statement[0]) == "import", statement
        imported = list(statement[2])
        for index, item in enumerate(imported):
            renamed = index + 1 < len(imported) and imported[index + 1] == hy.models.Keyword("as")
            if isinstance(item, hy.models.Keyword) or renamed:
                continue
            names.add(hy.mangle(str(item)))
    return frozenset(names)


_HELPER_NAMES = _helper_names()

_DEFK_HEADER = """
(require doeff-hy.macros [defk fnk do! <- traverse for/do])
(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(import doeff_traverse._effects [Reduce])
"""

# Head of a module with no defk and no import of `_doeff_do` -- checks that fnk / do! carry what they need themselves.
_BARE_HEADER = """
(require doeff-hy.macros [fnk do! <-])
"""


def _eval_module(code: str, header: str = _DEFK_HEADER) -> types.ModuleType:
    mod = types.ModuleType("fnk_do_bang_scope")
    sys.modules["fnk_do_bang_scope"] = mod
    for form in hy.read_many(header + code):
        hy.eval(form, mod.__dict__, module=mod)
    return mod


def _run(program: object) -> object:
    return run(scheduled(try_handler(fail_handler(sequential(program)))))


def test_helper_names_follow_helper_imports() -> None:
    """The table picks up the alias names of `_helper-imports` (the name after `:as`) and not the original names."""
    assert {
        "_doeff_do",
        "_doeff_effect_base",
        "_guard_statement_value",
        "_doeff_outcomes",
    } <= _HELPER_NAMES
    assert "do" not in _HELPER_NAMES
    assert "EffectBase" not in _HELPER_NAMES


_CASES = {
    # Shape of the request: fnk comes after traverse's read of `_doeff_do`
    "fnk-after-traverse": (
        """
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- coll (traverse (<- x (Iterate items)) (* x 2)))
          (<- total (Reduce (fnk [acc v] (+ acc v)) 0 coll))
          total)
        """,
        [1, 2, 3],
        12,
    ),
    "do-bang-after-for-do": (
        """
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- coll (for/do (<- x (From items)) (* x 3)))
          (<- n (do! (+ 1 1)))
          (<- total (Reduce (fnk [acc v] (+ acc v)) n coll))
          total)
        """,
        [1, 2, 3],
        20,
    ),
    # fnk comes after the read of the body-statement guard (_guard_statement_value)
    "fnk-after-statement-guard": (
        """
        (defk subject [x]
          {:pre [(: x int)] :post [(: % int)]}
          (abs x)
          (setv f (fnk [y] (+ y 1)))
          (<- r (f x))
          r)
        """,
        5,
        6,
    ),
    # After an fnk / do! on a branch that does not run, read the final-value guard (_doeff_effect_base)
    "fnk-in-untaken-branch": (
        """
        (defk subject [x]
          {:pre [(: x int)] :post [(: % int)]}
          (when (> x 100) (fnk [y] y))
          x)
        """,
        5,
        5,
    ),
    "do-bang-in-untaken-branch": (
        """
        (defk subject [x]
          {:pre [(: x int)] :post [(: % int)]}
          (when (> x 100) (do! x))
          x)
        """,
        5,
        5,
    ),
    # An inner do! comes after the guard of a statement inside do!
    "do-bang-inside-do-bang": (
        """
        (defk subject [x]
          {:pre [(: x int)] :post [(: % int)]}
          (<- r (do! (abs x) (<- y (do! 2)) (+ y x)))
          r)
        """,
        5,
        7,
    ),
}


@pytest.mark.parametrize(("source", "arg", "expected"), _CASES.values(), ids=_CASES.keys())
def test_fnk_and_do_bang_inside_defk_run(source: str, arg: object, expected: object) -> None:
    mod = _eval_module(source)
    assert _run(mod.subject(arg)) == expected


@pytest.mark.parametrize(("source", "arg", "expected"), _CASES.values(), ids=_CASES.keys())
def test_defk_body_binds_no_helper_name(source: str, arg: object, expected: object) -> None:
    """Helper names do not appear in the local names (or closure cells) of the defk body function -- if they did, the earlier read would be unbound."""
    mod = _eval_module(source)
    code = mod.subject.__doeff_generator_function__.__code__
    assert _HELPER_NAMES.isdisjoint(code.co_varnames + code.co_cellvars)


_HOSTS = {
    "fnk": "(defn host [] (fnk [x] (<- y (do! x)) (+ y (! (do! 1)))))",
    "do-bang": "(defn host [] (do! (abs 1) (<- y (do! 2)) y))",
    "do-bang-with-post": "(defn host [] (do! {:post [(: % int)]} (<- y (do! 2)) y))",
}


@pytest.mark.parametrize("source", _HOSTS.values(), ids=_HOSTS.keys())
def test_host_function_gets_no_local_name(source: str) -> None:
    """A function written with only fnk / do! has neither local names nor closure cells outside the unique names Hy generates
    (prefix `_hy_` -- names of functions inside the expansion) (an R2 test independent of the helper list)."""
    mod = _eval_module(source, _BARE_HEADER)
    code = mod.host.__code__
    assert [
        name for name in code.co_varnames + code.co_cellvars if not name.startswith("_hy_")
    ] == []


_BARE_CASES = {
    # fnk in a module without defk: <- and ! in the body do not rely on names in the module's globals
    "module-fnk-bind-and-bang": (
        """
        (setv weight (fnk [x] (* x 10)))
        (setv subject (fnk [acc v] (<- w (weight v)) (+ acc w (! (weight 1)))))
        (setv program (subject 1 2))
        """,
        31,
    ),
    "module-do-bang": (
        """
        (setv program (do! (abs -1) (<- y (do! 2)) (+ y 1)))
        """,
        3,
    ),
    "module-do-bang-with-post": (
        """
        (setv program (do! {:post [(: % int)]} (<- y (do! 2)) (+ y 1)))
        """,
        3,
    ),
    "defn-fnk-default-reads-local": (
        """
        (defn make []
          (setv base 4)
          (fnk [x [b base]] (<- y (do! x)) (+ y b)))
        (setv program ((make) 1))
        """,
        5,
    ),
    # fnk in a class body: default values are evaluated in the class body (the class's value is read even if the module has the same name)
    "class-fnk-default-reads-class-name": (
        """
        (setv RATE 1)
        (defclass Pricing []
          (setv RATE 3)
          (setv scale (fnk [x [rate RATE]] (* x rate))))
        (setv program ((. Pricing scale) 5))
        """,
        15,
    ),
    "class-fnk-default-reads-class-only-name": (
        """
        (defclass Pricing []
          (setv FACTOR 7)
          (setv scale (fnk [x [f FACTOR]] (<- y (do! x)) (* y f))))
        (setv program ((. Pricing scale) 5))
        """,
        35,
    ),
    "class-do-bang": (
        """
        (defclass Holder []
          (setv program (do! (abs -1) (<- y (do! 2)) (+ y 1))))
        (setv program (. Holder program))
        """,
        3,
    ),
}


@pytest.mark.parametrize(("source", "expected"), _BARE_CASES.values(), ids=_BARE_CASES.keys())
def test_fnk_and_do_bang_outside_defk_run(source: str, expected: object) -> None:
    """fnk / do! at module level, in a class body, and in a plain defn read the names where they are written, in a module with neither defk nor `_doeff_do`."""
    mod = _eval_module(source, _BARE_HEADER)
    assert run(mod.program) == expected
