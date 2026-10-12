"""handle・defhandler を module の段・class の本体・関数の中に書いた source を Python へ compile して出す(実行時と型検査)。"""
import ast
import sys
import types

import doeff_hy  # noqa: F401
import hy
from hy.compiler import hy_compile
from doeff_hy.static_view import static_view

SOURCE = """
(require doeff-hy.handle [handle defhandler])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(defclass [(dataclass :frozen True)] Bump [EffectBase] #^ int n)
(defclass [(dataclass :frozen True)] Other [EffectBase] #^ int n)
(defhandler top-h (Bump [n] (resume (+ n 1))))
(defhandler top-hp [base] "doc" (Bump [n] :when (> n 1) (resume (+ n base))) (Other [n] (try (resume n) (except [ValueError] (transfer 0)))))
(defhandler top-hs (session var calls 0) (session val c (abs 1)) (Bump [n] (:= calls (+ calls 1)) (resume (+ n calls c))))
(setv top-prog (handle (Bump 1) (Bump [n] :when (> n 0) (resume n)) (Other [n] (transfer n))))
(defn f [] (handle (Bump 1) (Bump [n] (resume n))) (defhandler fh (Bump [n] (resume n))) fh)
(defclass C []
  (setv prog (handle (Bump 1) (Bump [n] (resume n))))
  (defhandler h (Bump [n] (resume n)))
  (defhandler hp [b] (Bump [n] (resume b)))
  (defhandler hs (session var calls 0) (Bump [n] (:= calls (+ calls 1)) (resume calls))))
"""


def compile_text() -> str:
    mod = types.ModuleType("expand_probe")
    sys.modules["expand_probe"] = mod
    tree = hy_compile(hy.read_many(SOURCE), mod)
    return ast.unparse(tree)


mode = sys.argv[1]
if mode == "static":
    with static_view():
        print(compile_text())
else:
    print(compile_text())
