"""盲検 A の補足の再現: class の本体だけに defhandler を書いた module で、その handler を使うと NameError になるか(前からの事か)を見るため。"""

import sys
import types

import doeff_hy  # noqa: F401 — registers extensions
import hy

from doeff import run

SOURCE = """
(require doeff-hy.macros [do! <- defhandler])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(defclass [(dataclass :frozen True)] Bump [EffectBase] #^ int n)
(defclass Holder []
  (defhandler inner-h [base] (Bump [n] (resume (+ n base)))))
(setv program (do! (<- y (Bump 1)) y))
"""
mod = types.ModuleType("class_only_defhandler")
sys.modules["class_only_defhandler"] = mod
for form in hy.read_many(SOURCE):
    hy.eval(form, mod.__dict__, module=mod)
try:
    print("答え", run(mod.Holder.inner_h(5)(mod.program)))
except Exception as error:  # noqa: BLE001 — 再現の表示だけ
    print(f"{type(error).__name__}: {error}")
