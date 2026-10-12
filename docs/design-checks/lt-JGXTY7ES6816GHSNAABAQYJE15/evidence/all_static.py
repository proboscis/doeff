# 全定義の macro の検体を、関数の本体・class の本体に置いた投影で、import が束ねて残す名を数える
import ast, importlib.util, sys, tempfile
from pathlib import Path
spec = importlib.util.spec_from_file_location("nds", "tests/test_nested_definition_scope.py")
nds = importlib.util.module_from_spec(spec); sys.modules["nds"] = nds; spec.loader.exec_module(nds)
from doeff_hy.static_check import Projection, project
HEAD = ("(require doeff-hy.macros [defk deff defp defpp deftest defmcp-tool defeffect defhandler defsystem defpipeline <- traverse for/do])\n"
        "(require doeff-hy.record [defrecord defwire defenum])\n(require doeff-hy.conductor [defworkflow defphase agent!])\n"
        "(import dataclasses [dataclass])\n(import enum [StrEnum])\n(import doeff_hy)\n")
for macro, (source, _) in nds._DEFINITION_SAMPLES.items():
    for where, body in (("function", f"(defn subject [] {source} None)"), ("class", f"(defclass Outer [] {source})")):
        root = Path(tempfile.mkdtemp()); f = root / "s.hy"; f.write_text(HEAD + body + "\n", encoding="utf-8")
        p = project(root, [root], f)
        if not isinstance(p, Projection):
            print(f"{macro:12} {where:8} COMPILE-FAIL {p.diagnostic.message[:60] if hasattr(p,'diagnostic') else p}"); continue
        tree = ast.parse(p.text)
        scope = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in ("subject", "Outer"))
        print(f"{macro:12} {where:8} {sorted(nds._bound_by_imports(scope))}")
