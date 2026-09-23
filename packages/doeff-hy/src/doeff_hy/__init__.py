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

import hy.importer

from doeff_hy.ast_unparse import install as _install_ast_unparse

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
# 定数の値を複製しない同じ変換へ置き換える(理由と上流の報告は doeff_hy/ast_unparse.py)。
_install_ast_unparse()
