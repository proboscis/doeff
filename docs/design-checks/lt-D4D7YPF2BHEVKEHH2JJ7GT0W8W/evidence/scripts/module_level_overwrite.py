"""盲検 B の補足の再現: module の段の handle の import が、利用者の module の同じ名(Pass)を上書きするかを見るため。"""

import sys
import types

import doeff_hy  # noqa: F401
import hy

SOURCE = """
(require doeff-hy.macros [do! <- handle])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(defclass [(dataclass :frozen True)] Pass [EffectBase])
(setv mine Pass)
(defclass [(dataclass :frozen True)] Bump [EffectBase] #^ int n)
(setv program (handle (do! 1) (Bump [n] (resume n))))
"""
mod = types.ModuleType("module_level_overwrite")
sys.modules["module_level_overwrite"] = mod
for form in hy.read_many(SOURCE):
    hy.eval(form, mod.__dict__, module=mod)
print("handle の後の Pass は利用者の class か:", mod.Pass is mod.mine, "・Pass の module:", mod.Pass.__module__)
