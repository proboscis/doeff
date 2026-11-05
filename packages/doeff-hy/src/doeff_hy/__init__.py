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

# Do not use stale bytecode of Hy modules whose macros have changed. The primary place for this is the venv startup
# doeff_hy_bytecode_guard.pth; this is the second one, for startups where .pth is not read (python -S etc.) (the second call does nothing).
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

# Hy's replacement of `ast.unparse` recurses without end when combined with Python 3.14's annotationlib.
# Replace it with the same conversion that does not duplicate constant values (reason and upstream report: doeff_hy/_ast_unparse.py).
_install_ast_unparse()

# Swap the inspect.stack called by Hy's require family for a lighter version (non-Hy callers get the original function; reason: doeff_hy/_light_stack.py).
_install_light_stack()


# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers. A root that already has `__all__`
# lists the names there too; the tool does not add `__all__` to a root without one (DOEFF021).
# The parts of doeff_hy._record_runtime that the defrecord expansion calls (RECORD_DOC, record_repr, etc.) are not exposed from the entry point — the expansion reads the `_`
# path. The doeff-hy-pytest plugin reads the install of doeff_hy._ast_unparse from the `_` path.
if TYPE_CHECKING:
    from doeff_hy._http import http_get as http_get
    from doeff_hy._json_lines import jsonl_lines as jsonl_lines
    from doeff_hy._json_lines import whole_lines_end as whole_lines_end
    from doeff_hy._pytest_items import MalformedRecord as MalformedRecord
    from doeff_hy._static_baseline import BaselineUnreadable as BaselineUnreadable
    from doeff_hy._static_carry import CarryUnreadableError as CarryUnreadableError
    from doeff_hy._static_check import ExpansionWorkerLostError as ExpansionWorkerLostError
    from doeff_hy._static_stub import StubUnmade as StubUnmade
    from doeff_hy._typecheck_handlers import type_checks_off as type_checks_off
    from doeff_hy._typecheck_handlers import type_checks_on as type_checks_on
    from doeff_hy._wire import dump as dump
    from doeff_hy._wire import dump_json as dump_json
    from doeff_hy._wire import json_schema as json_schema
    from doeff_hy._wire import parse as parse
    from doeff_hy._wire import parse_json as parse_json

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
    # program helpers used by outside repos (ADR-DOE-API-001 R18, R25)
    "http_get": "doeff_hy._http",
    "jsonl_lines": "doeff_hy._json_lines",
    "whole_lines_end": "doeff_hy._json_lines",
    "dump": "doeff_hy._wire",
    "dump_json": "doeff_hy._wire",
    "json_schema": "doeff_hy._wire",
    "parse": "doeff_hy._wire",
    "parse_json": "doeff_hy._wire",
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
