"""import の口が root A で書いた .pyc の頭の後ろの bytes と、保存先の entry の中身と、entry を読んで marshal し直した bytes を比べる。"""
import importlib.util, marshal, sys
from pathlib import Path
W, WT = Path(sys.argv[1]), Path(sys.argv[2])
base = W / "exp"; store = base / "store"; A = base / "rootA" / "repo"
spec = importlib.util.spec_from_file_location("exp_code_store", WT / "packages/doeff-hy/src/doeff_hy_bytecode_guard/code_store.py")
cs = importlib.util.module_from_spec(spec); spec.loader.exec_module(cs)
import hy
src = (A / "lazy/thing.hy").read_bytes()
key = cs.code_key(str(A / "lazy/thing.hy"), src, "lazy.thing", hy.__version__, sys.implementation.cache_tag or "", sys.flags.optimize)
found = cs.current_entry(str(store), key, lambda c: True)
body_a = (A / "lazy/__pycache__" / f"thing.{sys.implementation.cache_tag}.pyc").read_bytes()[16:]
print("entry co_filename:", found.code.co_filename)
print("rootA pyc body == entry data:", body_a == found.data, len(body_a), len(found.data))
print("marshal.dumps(entry code) == entry data:", marshal.dumps(found.code) == found.data)
