"""S2 の試し(盲検 A の変更要求): 節の操作 (throw 例外) を handler-imports と handler-expr に足した時に、関数の中の handle が
外の関数に名を作らない事と、Raise の節の throw をどの検めも止めない事(盲検 A の b)を見るため。

走らせ方: cd <S2 の差分を当てた doeff の作業木> && uv run python -I <この file>
"""

import sys
import types

import doeff_hy  # noqa: F401 — registers extensions
import hy
from doeff_core_effects import try_handler
from doeff_core_effects.scheduler import scheduled
from doeff_traverse.handlers import fail_handler, sequential

from doeff import run

HEADER = """
(require doeff-hy.macros [defk do! <- traverse handle])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_core_effects.effects [Raise])
(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(defclass [(dataclass :frozen True)] Bump [EffectBase] #^ int n)
(defk guarded [n] {:pre [(: n int)] :post [(: % int)]}
  (try (do (<- y (Bump n)) y) (except [e ValueError] -1)))
(defk raises [] {:pre [] :post [(: % int)]}
  (try (do (<- (Raise "x")) 0) (except [e ValueError] 42)))
"""

CASES = {
    "throw-after-traverse": """
(defk subject [items] {:pre [(: items list)] :post [(: % int)]}
  (<- coll (traverse (<- x (Iterate items)) (* x 2)))
  (<- r (handle (guarded (sum coll)) (Bump [n] (throw (ValueError "boom")))))
  r)
""",
    "raise-clause-throws": """
(defk subject [items] {:pre [(: items list)] :post [(: % int)]}
  (<- r (handle (raises) (Raise [reason] (throw (ValueError "into-k")))))
  r)
""",
}


def fresh(code: str) -> types.ModuleType:
    """形ごとに module を分けるため。"""
    mod = types.ModuleType("s2_throw")
    sys.modules["s2_throw"] = mod
    for form in hy.read_many(HEADER + code):
        hy.eval(form, mod.__dict__, module=mod)
    return mod


for label, code in CASES.items():
    try:
        mod = fresh(code)
        inner = mod.subject.__doeff_generator_function__.__code__
        own = [n for n in inner.co_varnames + inner.co_cellvars if not n.startswith("_hy_")]
        result = run(scheduled(try_handler(fail_handler(sequential()(mod.subject([1, 2, 3]))))))
        print(f"{label}: 答え {result!r}・外の関数の局所(_hy_ の外){own}")
    except BaseException as error:  # noqa: BLE001 — 再現の表示だけ
        print(f"{label}: {type(error).__name__}: {str(error)[-160:]}")
