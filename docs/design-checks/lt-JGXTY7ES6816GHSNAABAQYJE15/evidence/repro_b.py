# 盲検 B の手順 2: class の本体の defrecord(:tags つき)の投影で、入れ子の Span が読む名のうち、外の class の import が束ねる物
import ast, pathlib, tempfile
from doeff_hy.static_check import Projection, project
SOURCE = '''(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(defclass Outer [] (defrecord Span {:tags {:context "t" :role "type"}} #^ int start))
'''
root = pathlib.Path(tempfile.mkdtemp()); src = root / "class_body_tags.hy"; src.write_text(SOURCE)
p = project(root, [root], src); assert isinstance(p, Projection), p
tree = ast.parse(p.text)
outer = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Outer")
span = next(n for n in outer.body if isinstance(n, ast.ClassDef) and n.name == "Span")
bound = lambda b: {a.asname or a.name.split(".")[0] for n in b if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
mod = bound(tree.body) | {n.name for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
reads = {n.id for n in ast.walk(span) if isinstance(n, ast.Name)}
print(sorted(bound(outer.body)), sorted((reads & bound(outer.body)) - mod))
