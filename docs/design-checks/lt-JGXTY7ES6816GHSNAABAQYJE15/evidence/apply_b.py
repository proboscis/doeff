# 盲検 B の差分を record.hy へ当てる(使い捨て — 確かめたら git checkout で戻す)
from pathlib import Path
p = Path("/home/ubuntu/.worktrees/doeff-wt-scoped-definition/packages/doeff-hy/src/doeff_hy/record.hy")
s = p.read_text(encoding="utf-8")
anchor1 = "  (setv written (when (not static-view) (class-form docstring class-vars fields post-init)))\n  (_scoped-definition _hy-compiler name\n    [`(do\n        (import doeff_hy.declarations doeff_hy.record ~@(if (is written None) [] ['doeff_hy.record_runtime]))\n"
assert s.count(anchor1) == 1
insert = """  (setv tag-class-vars (if (is tags None)
                           []
                           (list `[#^ (get _doeff-ClassVar doeff_hy.declarations.DefinitionTags) __doeff-tags__]))
        class-var-import (if (and tag-class-vars (is class-vars None))
                             ['(import typing [ClassVar :as _doeff-ClassVar])]
                             [])
        class-vars (+ (list (or class-vars [])) tag-class-vars))
"""
new = insert + anchor1 + "        ~@class-var-import\n"
p.write_text(s.replace(anchor1, new), encoding="utf-8")
print("applied")
