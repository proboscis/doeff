# 型検査のための展開だけを新しい process で出す(実行時の展開の gensym が番号をずらさないように)
"""module・class の段の handle / defhandler の展開(実行時と型検査のための展開)を Python の source にして出す。

直す前と後の木で走らせ、出力の差を見る — 関数の外の展開が 1 字も変わらない事を確かめるため。
"""

import ast
import sys
import types

import doeff_hy  # noqa: F401
import hy
from doeff_hy.static_view import static_view

SOURCE = """
(require doeff-hy.macros [traverse])
(require doeff-hy.macros [defk do! <- handle defhandler])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(defclass [(dataclass :frozen True)] Bump [EffectBase] #^ int n)
(defhandler plain-h "doc" {:effects [Bump]} (Bump [n] (resume (+ n 1))))
(defhandler param-h [base] (Bump [n] :when (> n 0) (resume (+ n base))))
(defhandler lazy-h (session var calls 0) (Bump [n] (:= calls (+ calls 1)) (resume calls)))
(defhandler fence-h (EffectBase [] :when (not (isinstance effect Bump)) (resume 0)))
(defclass Holder []
  (defhandler inner-h [base] (Bump [n] (resume (+ n base))))
  (setv prog (handle (do! 1) (Bump [n] (resume n)))))
(defk subject [items] {:pre [(: items list)] :post [(: % int)]} (<- coll (traverse (<- x (Iterate items)) (* x 2))) (defhandler in-h [b] (Bump [n] (resume b))) (<- r (handle (do! 1) (Bump [n] (resume (+ n 100))))) r)
"""


def expand() -> str:
    """SOURCE の各 form を Hy の compiler に通し、Python の source にして繋ぐ。"""
    forms = list(hy.read_many(SOURCE))
    target = types.ModuleType("expand_target")
    sys.modules["expand_target"] = target
    module = hy.compiler.hy_compile(hy.models.Lazy(iter(forms)), target, filename="<expand>")
    return ast.unparse(module)



with static_view():
    static = expand()


print("=== static ===")
print(static)
