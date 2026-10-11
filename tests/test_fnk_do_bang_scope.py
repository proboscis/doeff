"""defk の本体の中の fnk・do! が、外の関数に局所の名を作らない事の検(card ki-043ea763c926 の所見)。

fnk・do! の実行時の展開は、`_doeff_do`・`_guard_statement_value`・`_doeff_effect_base` などの補助を import する。
defk の本体(関数)の中でそれを import すると、名が関数全体の局所の名になり、同じ関数で先に読む traverse / for/do の
`_doeff_do`、本体の文の guard の `_guard_statement_value`、最後の値の guard の `_doeff_effect_base` が
UnboundLocalError で落ちていた。fold の素直な置き換え `(Reduce (fnk [acc v] …) init coll)` がちょうどこの位置に来る。
"""

import sys
import types

import doeff_hy  # noqa: F401 — registers extensions
import hy
import hy.macros
import pytest
from doeff_core_effects import try_handler
from doeff_core_effects.scheduler import scheduled
from doeff_traverse.handlers import fail_handler, sequential

from doeff import run

_HELPER_NAMES = frozenset(
    {
        "_doeff_do",
        "_doeff_effect_base",
        "_install_guard_globals",
        "_guard_performed",
        "_guard_statement_value",
        "_doeff_check_program_return",
    }
)

_HEADER = """
(require doeff-hy.macros [defk fnk do! <- traverse for/do])
(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(import doeff_traverse.effects [Reduce])
"""


def _eval_module(code: str) -> types.ModuleType:
    mod = types.ModuleType("fnk_do_bang_scope")
    sys.modules["fnk_do_bang_scope"] = mod
    for form in hy.read_many(_HEADER + code):
        hy.eval(form, mod.__dict__, module=mod)
    return mod


def _run(program: object) -> object:
    return run(scheduled(try_handler(fail_handler(sequential()(program)))))


_CASES = {
    # 依頼の形: traverse の `_doeff_do` の読みの後に fnk の import が来る
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
    # 本体の文の guard(_guard_statement_value)の読みの後に fnk の import が来る
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
    # 走らない枝の fnk / do! の後に、最後の値の guard(_doeff_effect_base)を読む
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
}


@pytest.mark.parametrize(("source", "arg", "expected"), _CASES.values(), ids=_CASES.keys())
def test_fnk_and_do_bang_inside_defk_run(source: str, arg: object, expected: object) -> None:
    mod = _eval_module(source)
    assert _run(mod.subject(arg)) == expected


@pytest.mark.parametrize(("source", "arg", "expected"), _CASES.values(), ids=_CASES.keys())
def test_defk_body_binds_no_helper_name(source: str, arg: object, expected: object) -> None:
    """fnk・do! の補助の名は、defk の本体の関数の局所(と閉包の cell)に現れない — 現れると先の読みが未束縛になる。"""
    mod = _eval_module(source)
    code = mod.subject.__doeff_generator_function__.__code__
    assert _HELPER_NAMES.isdisjoint(code.co_varnames + code.co_cellvars)
