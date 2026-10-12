"""effect analyzer の展開(_expand_hy — 実物)を thread pool で並べた時、関数の中の require の macro が記録から漏れるかを数える(作業用)。"""
import os, sys, tempfile, uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
os.environ["DOEFF_HY_CODE_STORE"] = "off"
sys.dont_write_bytecode = True
sys.path.insert(0, "/work/toolset/worktrees/doeff-wt-blind-b-lt07hr/packages/doeff-effect-analyzer/python")
from doeff_effect_analyzer.hy_expansion import _expand_hy

root = Path(tempfile.mkdtemp(dir="/tmp/blind-b-lt07hr", prefix="ana-"))
pkg = f"blindb_{uuid.uuid4().hex[:8]}"
(root / pkg).mkdir()
(root / pkg / "__init__.py").write_text("")
(root / pkg / "m.hy").write_text("(defmacro answer [] 11)\n")
sys.path.insert(0, str(root))
quiet = "".join(f"(defn q{i} [x] (lfor y (range x) (fn [] y)))\n" for i in range(200))
local = f"(defn f []\n  (require {pkg}.m [answer])\n  (answer))\n(setv value (f))\n"
jobs = [(src, str(root / pkg / f"{kind}{i}.hy"), f"{pkg}.{kind}{i}") for i in range(40) for kind, src in (("quiet", quiet), ("local", local))]
with ThreadPoolExecutor(max_workers=8) as pool:
    done = list(pool.map(lambda job: (job[2], _expand_hy(*job)), jobs))
missed = [n for n, e in done if ".local" in n and not any(u.name == "answer" for u in e.macros.macros)]
mixed = [n for n, e in done if ".quiet" in n and any(u.name == "answer" for u in e.macros.macros)]
print(f"_expand_hy の local 40 本のうち answer の漏れ: {len(missed)} 本 / quiet 40 本のうち混入: {len(mixed)} 本")
