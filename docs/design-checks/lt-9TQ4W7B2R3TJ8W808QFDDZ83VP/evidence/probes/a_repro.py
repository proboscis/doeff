"""盲検 A の再現: 常駐の process で、読み込み済みの値の module(tables.py)を書き換えた後に Hy の使い手を compile すると、
展開(process の中の古い値)と記録の digest(今の disk の文)が食い違うか。引数: doeff の木の venv の python・作業 dir。"""
import json, shutil, subprocess, sys, tempfile
from pathlib import Path

python, work = (sys.executable if sys.argv[1] == "-" else sys.argv[1]), Path(sys.argv[2])  # "-" = この python
if work.exists():
    shutil.rmtree(work)
root = work / "tree"
pkg = "probe_a"
(root / pkg).mkdir(parents=True)
(root / pkg / "__init__.py").write_text("")
(root / pkg / "tables.py").write_text('ROWS = {"a": 1}\nROWS["b"] = 2\n')
(root / pkg / "macros.hy").write_text(f"(import {pkg}.tables :as tables)\n(defmacro rows [] (get tables.ROWS \"b\"))\n")
for user in ("user_0", "user_1"):
    (root / pkg / f"{user}.hy").write_text(f"(require {pkg}.macros [rows])\n(setv value (rows))\n")
CHILD = r'''
import importlib, importlib.machinery, json, sys
from pathlib import Path
root, pkg, *steps = sys.argv[1], sys.argv[2], *sys.argv[3:]
sys.path.insert(0, root)
import hy
compiled = []
previous = importlib.machinery.SourceFileLoader.source_to_code
def counting(self, data, path, *args, **kwargs):
    code = previous(self, data, path, *args, **kwargs)
    if isinstance(path, str) and path.endswith(".hy"):
        compiled.append(Path(path).name)
    return code
importlib.machinery.SourceFileLoader.source_to_code = counting
import doeff_hy
values = {}
for step in steps:
    if step.startswith("edit:"):
        _, name, old, new = step.split(":")
        target = Path(root) / pkg / name
        target.write_text(target.read_text().replace(old, new))
        importlib.invalidate_caches()
    else:
        values[step] = importlib.import_module(f"{pkg}.{step}").value
print(json.dumps({"compiled": compiled, "values": values}))
'''
env_extra = {"DOEFF_HY_CODE_STORE": str(work / "store"), "PYTHONPYCACHEPREFIX": str(work / "pycache")}
import os
env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"} | env_extra
def run(*steps):
    done = subprocess.run([python, "-c", CHILD, str(root), pkg, *steps], capture_output=True, text=True, env=env, timeout=120)
    if done.returncode:
        print(done.stderr[-2000:]); raise SystemExit(1)
    return json.loads(done.stdout.strip().splitlines()[-1])
print("1 全部を compile:", run("user_0", "user_1"))
print("2 user_0 を読み、tables.py を書き換え、user_1 を読む(同じ process):", run("user_0", 'edit:tables.py:ROWS["b"] = 2:ROWS["b"] = 20', "user_1"))
print("3 新しい process で user_1:", run("user_1"))
shutil.rmtree(work / "pycache"); shutil.rmtree(work / "store")
print("4 対照(pycache と置き場を消して user_1):", run("user_1"))
