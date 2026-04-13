"""doeff-hy — Standard Hy macros for doeff effect composition.

Usage in .hy files:
    (require doeff-hy.macros [do! defk deff fnk <- ! defp defpp defprogram traverse for/do])
    (import doeff [do :as _doeff-do])

File extensions:
    .hy  — general Hy source (interpreters, effects, utilities)
    .hyk — kleisli modules (defk, deff, defhandler only — no defp)
    .hyp — program modules (defp, defprogram entrypoints)
"""
import importlib.machinery
import os

import hy.importer  # noqa: F401 — activates Hy import hooks

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
