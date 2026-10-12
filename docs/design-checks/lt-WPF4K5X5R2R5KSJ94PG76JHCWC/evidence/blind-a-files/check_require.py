"""light_stack(path で渡す)を入れた上で、Hy の require(hy.macros.require が inspect.stack を読む)が通るかを確かめる。"""
import importlib.util, sys, tempfile, types
from pathlib import Path

spec = importlib.util.spec_from_file_location("light_stack_under_test", sys.argv[1])
ls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ls)
import hy
print("hy", hy.__version__)
ls.install()
d = Path(tempfile.mkdtemp())
(d / "blind_macros_src.hy").write_text("(defmacro twice [x] `(* 2 ~x))\n")
sys.path.insert(0, str(d))
m = types.ModuleType("blind_target")
try:
    print("require ->", hy.eval(hy.read_many("(require blind-macros-src [twice]) (twice 21)"), module=m))
except Exception as e:
    print("require failed:", type(e).__name__, [l for l in str(e).splitlines() if "Error" in l][-1:])
