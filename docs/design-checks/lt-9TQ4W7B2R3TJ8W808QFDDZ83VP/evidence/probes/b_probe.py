"""盲検 B の probe: 別の file の macro(defs.hy の defhelper)が作る補助関数 helper を、macro body が展開の時に呼ぶ。defs.hy を
変えると macros.hy が compile し直され helper の code が変わる — 使い手 user の記録は古いと判じられ、value が変わるか。
引数: doeff の木の venv の python・作業 dir。"""
import json, os, shutil, subprocess, sys
from pathlib import Path

python, work = (sys.executable if sys.argv[1] == "-" else sys.argv[1]), Path(sys.argv[2])  # "-" = この python
if work.exists():
    shutil.rmtree(work)
root = work / "tree"
pkg = "facts_probe"
(root / pkg).mkdir(parents=True)
(root / pkg / "__init__.py").write_text("")
(root / pkg / "defs.hy").write_text("(defmacro defhelper [name value] `(defn ~name [] ~value))\n")
(root / pkg / "macros.hy").write_text(f"(require {pkg}.defs [defhelper])\n(defhelper helper 1)\n(defmacro body [] (helper))\n")
(root / pkg / "user.hy").write_text(f"(require {pkg}.macros [body])\n(setv value (body))\n")
CHILD = r'''
import importlib, importlib.machinery, json, sys
from pathlib import Path
root, pkg = sys.argv[1], sys.argv[2]
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
value = importlib.import_module(f"{pkg}.user").value
print(json.dumps({"compiled": compiled, "value": value}))
'''
env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"} | {
    "DOEFF_HY_CODE_STORE": str(work / "store"), "PYTHONPYCACHEPREFIX": str(work / "pycache")}
def run():
    done = subprocess.run([python, "-c", CHILD, str(root), pkg], capture_output=True, text=True, env=env, timeout=120)
    if done.returncode:
        print(done.stderr[-2000:]); raise SystemExit(1)
    return json.loads(done.stdout.strip().splitlines()[-1])
print("1 初めの process:", run())
print("2 2 つ目の process(置き場が温まる):", run())
defs = root / pkg / "defs.hy"
defs.write_text(defs.read_text().replace("[] ~value)", "[] (* ~value 10))"))
print("3 defs.hy を替えた後の process:", run())
shutil.rmtree(work / "pycache"); shutil.rmtree(work / "store")
print("4 対照(pycache と置き場を消す):", run())
