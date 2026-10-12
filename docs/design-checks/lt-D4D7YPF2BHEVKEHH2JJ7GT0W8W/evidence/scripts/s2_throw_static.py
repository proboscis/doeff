"""S2 の試しの型検査のための展開: 節の操作 throw を足した時、型検査のための展開で TransferThrow が import されずに読まれるか(盲検 A の d)を見るため。"""

import ast
import sys
import types

import doeff_hy  # noqa: F401
import hy
from doeff_hy.static_view import static_view

SOURCE = """
(require doeff-hy.macros [defk <- handle])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(defclass [(dataclass :frozen True)] Bump [EffectBase] #^ int n)
(defk subject [n] {:pre [(: n int)] :post [(: % int)]}
  (<- r (handle (subject n) (Bump [m] (throw (ValueError "boom")))))
  r)
"""
target = types.ModuleType("s2_throw_static")
sys.modules["s2_throw_static"] = target
with static_view():
    tree = hy.compiler.hy_compile(hy.models.Lazy(iter(hy.read_many(SOURCE))), target, filename="<x>")
text = ast.unparse(tree)
print("TransferThrow を読む行:", [line.strip() for line in text.splitlines() if "TransferThrow" in line])
print("TransferThrow の import:", [line.strip() for line in text.splitlines() if "import" in line and "TransferThrow" in line])
