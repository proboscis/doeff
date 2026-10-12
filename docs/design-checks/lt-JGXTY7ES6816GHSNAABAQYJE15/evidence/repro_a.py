# 盲検 A の失敗例の再現: class の本体・関数の本体に書いた定義の投影で、import が束ねる名と、入れ子の scope が読む名(symtable)
import ast, symtable, sys, tempfile
from pathlib import Path
import doeff_hy.macros, doeff_hy.record
from doeff_hy.static_check import Projection, project

root = Path(tempfile.mkdtemp())
(root / "outer.hy").write_text("""(require doeff-hy.macros [defeffect])
(require doeff-hy.record [defrecord defwire])
(import dataclasses [dataclass])
(defclass OuterWire [] (defwire Span {:names :camel} #^ int start-at))
(defclass OuterRecord [] (defrecord Span {:check [(>= start 0)]} #^ int start))
(defclass OuterEffect [] (defeffect Inner {:fields [(: x int)] :answer int :tags {:context "t" :role "intent"}}))
(defn in-function [] (defrecord Span {:check [(>= start 0)]} #^ int start) (defwire Wire {:names :camel} #^ int start-at) None)
""", encoding="utf-8")
p = project(root, [root], root / "outer.hy")
assert isinstance(p, Projection), p
tree = ast.parse(p.text)
bound = {a.asname or a.name.split(".")[0] for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
print("module binds:", sorted(x for x in bound if x.startswith("_doeff_")))
for node in tree.body:
    if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
        names = sorted({a.asname or a.name.split(".")[0] for n in ast.walk(node) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names})
        print(type(node).__name__, node.name, "import で束ぐ名:", names)
WATCH = {"_doeff_ClassVar", "_doeff_cast"}
def walk(t, path):
    here = path + [t.get_name()]
    for s in t.get_symbols():
        if s.get_name() in WATCH and s.is_referenced():
            print("  symtable", "/".join(here), s.get_name(), "free" if s.is_free() else "global" if s.is_global() else "local")
    for c in t.get_children():
        walk(c, here)
walk(symtable.symtable(p.text, "projection", "exec"), [])
