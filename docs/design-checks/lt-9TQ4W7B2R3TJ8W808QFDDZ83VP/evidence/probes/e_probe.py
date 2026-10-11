"""盲検 A の付録の候補(B1 の条 3 の別の形): macro が読む大域の dict を環境変数から組む。環境変数だけを替えた新しい process は、
使い手の .pyc を古いと判じて展開し直すか。比べに素の文字列(条 3 の形)も置く。引数: 作業 dir(子は同じ python で走る)。"""
import json, os, shutil, subprocess, sys
from pathlib import Path

work = Path(sys.argv[1])
if work.exists():
    shutil.rmtree(work)
root = work / "tree"
pkg = "probe_e"
(root / pkg).mkdir(parents=True)
(root / pkg / "__init__.py").write_text("")
(root / pkg / "macros.hy").write_text(
    '(import os)\n'
    '(setv MODE (os.environ.get "PROBE_E_MODE" "plain"))\n'
    '(setv MODES {"a" (os.environ.get "PROBE_E_MODE" "plain")})\n'
    '(defmacro moded [] MODE)\n'
    '(defmacro moded-table [] (get MODES "a"))\n')
(root / pkg / "plain_user.hy").write_text(f"(require {pkg}.macros [moded])\n(setv value (moded))\n")
(root / pkg / "table_user.hy").write_text(f"(require {pkg}.macros [moded-table])\n(setv value (moded-table))\n")
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
values = {name: importlib.import_module(f"{pkg}.{name}").value for name in ("plain_user", "table_user")}
print(json.dumps({"compiled": compiled, "values": values}))
'''
base = {k: v for k, v in os.environ.items() if k not in ("PYTHONDONTWRITEBYTECODE", "PROBE_E_MODE")} | {
    "DOEFF_HY_CODE_STORE": str(work / "store"), "PYTHONPYCACHEPREFIX": str(work / "pycache")}
def run(**extra):
    done = subprocess.run([sys.executable, "-c", CHILD, str(root), pkg], capture_output=True, text=True,
                          env=base | extra, timeout=120)
    if done.returncode:
        print(done.stderr[-2000:]); raise SystemExit(1)
    return json.loads(done.stdout.strip().splitlines()[-1])
print("1 環境変数なし(plain):", run())
print("2 同じ環境の 2 つ目の process:", run())
print("3 PROBE_E_MODE=fancy の process:", run(PROBE_E_MODE="fancy"))
