"""doeff-hy — Standard Hy macros for doeff effect composition.

Usage in .hy files:
    (require doeff-hy.macros [do! defk deff fnk <- ! defp defpp deftest traverse for/do])
    (import doeff [do :as _doeff-do])

File extensions:
    .hy  — general Hy source (interpreters, effects, utilities)
    .hyk — kleisli modules (defk, deff, defhandler, deftest — no defp)
    .hyp — program modules (defp, defpp, deftest entrypoints)
"""
import importlib.machinery
import os
from importlib import import_module
from typing import TYPE_CHECKING

import doeff_hy_bytecode_guard
import hy.importer

from doeff_hy._ast_unparse import install as _install_ast_unparse
from doeff_hy._light_stack import install as _install_light_stack

# macro が変わった Hy の module の古い bytecode を使わない(agora-redesign #1292)。本来の入れ所は venv の起動時の
# doeff_hy_bytecode_guard.pth で、ここは .pth が読まれない起動(python -S 等)のための 2 つ目(2 度目は何もしない)。
doeff_hy_bytecode_guard.install()

# Register .hyk and .hyp as Hy source extensions
for _ext in (".hyk", ".hyp"):
    if _ext not in importlib.machinery.SOURCE_SUFFIXES:
        importlib.machinery.SOURCE_SUFFIXES.insert(0, _ext)

# Patch Hy's source detection to recognise .hyk/.hyp as Hy (not Python)
_HY_EXTENSIONS = {".hy", ".hyk", ".hyp"}
def _could_be_hy_src(filename):
    return os.path.isfile(filename) and (
        os.path.splitext(filename)[1]
        not in set(importlib.machinery.SOURCE_SUFFIXES) - _HY_EXTENSIONS
    )


hy.importer._could_be_hy_src = _could_be_hy_src

# Hy の `ast.unparse` の差し替えが Python 3.14 の annotationlib と組むと止まらない再帰になる。
# 定数の値を複製しない同じ変換へ置き換える(理由と上流の報告は doeff_hy/_ast_unparse.py)。
_install_ast_unparse()

# Hy の require 系が呼ぶ inspect.stack を軽い版へ(Hy 以外の呼び出しは元の関数。理由は doeff_hy/_light_stack.py)。
_install_light_stack()


# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers. A root that already has `__all__`
# lists the names there too; the tool does not add `__all__` to a root without one (DOEFF021).
# defrecord の展開が呼ぶ doeff_hy._record_runtime の部品(RECORD_DOC・record_repr ほか)と、doeff_hy._pytest_plugin の
# install(doeff_hy._ast_unparse の install と同じ物)は入口から出さない — 展開と plugin は `_` の path を読む(card ki-797d1e7b122b)。
if TYPE_CHECKING:
    from doeff_hy._pytest_items import MalformedRecord as MalformedRecord
    from doeff_hy._static_baseline import BaselineUnreadable as BaselineUnreadable
    from doeff_hy._static_carry import CarryUnreadableError as CarryUnreadableError
    from doeff_hy._static_check import ExpansionWorkerLostError as ExpansionWorkerLostError
    from doeff_hy._static_stub import StubUnmade as StubUnmade
    from doeff_hy._typecheck_handlers import type_checks_off as type_checks_off
    from doeff_hy._typecheck_handlers import type_checks_on as type_checks_on

_LAZY_EXPORTS: dict[str, str] = {
    "MalformedRecord": "doeff_hy._pytest_items",
    "BaselineUnreadable": "doeff_hy._static_baseline",
    "CarryUnreadableError": "doeff_hy._static_carry",
    "ExpansionWorkerLostError": "doeff_hy._static_check",
    "StubUnmade": "doeff_hy._static_stub",
    "TypeCheck": "doeff_hy._typecheck",
    "TypeCheckError": "doeff_hy._typecheck",
    "type_check": "doeff_hy._typecheck",
    "type_checks_off": "doeff_hy._typecheck_handlers",
    "type_checks_on": "doeff_hy._typecheck_handlers",
    "ClauseEndingError": "doeff_hy._clause_endings",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
