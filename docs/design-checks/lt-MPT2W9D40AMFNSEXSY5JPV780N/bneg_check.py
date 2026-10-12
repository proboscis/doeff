"""盲検 B の害の形: 書き手が class の本体に置いた `_doeff_` の頭の別名の import は、投影で module の段へ移さない(閉じた表)。"""
import ast
import sys
from pathlib import Path

import doeff_hy.macros  # noqa: F401
from doeff_hy.static_check import Projection, project

root = Path(sys.argv[1])
src = root / "author_class_body_import.hy"
src.write_text(
    "(import doeff [do :as _doeff-do])\n"
    "(defclass Holder []\n"
    "  (import doeff_traverse [Traverse :as _doeff_traverse_Traverse])\n"
    "  (import doeff [do :as _doeff-do])\n"
    "  (setv kind _doeff_traverse_Traverse))\n",
    encoding="utf-8",
)
p = project(root, [root], src)
assert isinstance(p, Projection)
tree = ast.parse(p.text)
holder = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Holder")
inside = sorted(a.asname or a.name for n in ast.walk(holder) if isinstance(n, ast.ImportFrom) for a in n.names)
print("class の本体に残る書き手の import:", inside)
assert inside == ["_doeff_do", "_doeff_traverse_Traverse"], inside
print("OK: 書き手の _doeff_ の頭の import は移さない")
