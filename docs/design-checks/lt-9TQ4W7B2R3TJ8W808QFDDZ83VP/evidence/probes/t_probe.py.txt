"""盲検 A の付録の候補(S3・S5): free-threaded の 3.14 で、一番外の読み込みの中で起きた thread は context(読み込みの覚え)を継ぐ。
読み込みが終わった後に値の module(tables.py)を書き換えて reload し、その thread が Hy の使い手を読むと、継いだ古い写しで覚えを
確かめて今に合うと判じるか。対照は同じ手順で本 thread が読む。引数: doeff の木の venv の python・作業 dir。"""
import json, os, shutil, subprocess, sys
from pathlib import Path

python, work = (sys.executable if sys.argv[1] == "-" else sys.argv[1]), Path(sys.argv[2])  # "-" = この python
if work.exists():
    shutil.rmtree(work)
root = work / "tree"
pkg = "probe_t"
(root / pkg).mkdir(parents=True)
(root / pkg / "__init__.py").write_text("")
(root / pkg / "tables.py").write_text('ROWS = {"a": 1}\n')
(root / pkg / "macros.hy").write_text(f"(import {pkg}.tables :as tables)\n(defmacro rows [] (get tables.ROWS \"a\"))\n")
for user in ("user_0", "user_1"):
    (root / pkg / f"{user}.hy").write_text(f"(require {pkg}.macros [rows])\n(setv value (rows))\n")
(root / pkg / "starter.py").write_text(f'''
import importlib, queue, threading
import {pkg}.user_0  # この一番外の読み込みの中で照らす(覚えと file の状態の写しができる)
requests, answers = queue.Queue(), queue.Queue()
def serve():
    while (name := requests.get()) is not None:
        answers.put(importlib.import_module("{pkg}." + name).value)
threading.Thread(target=serve, daemon=True).start()  # 読み込みの中で起きた thread(context を継ぐ)
''')
CHILD = r'''
import importlib, importlib.machinery, json, sys
from pathlib import Path
root, pkg, who = sys.argv[1], sys.argv[2], sys.argv[3]
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
if who == "warm":
    for name in ("user_0", "user_1"):
        importlib.import_module(f"{pkg}.{name}")
    print(json.dumps({"compiled": compiled})); raise SystemExit
starter = importlib.import_module(f"{pkg}.starter")
tables = importlib.import_module(f"{pkg}.tables")
target = Path(root) / pkg / "tables.py"
target.write_text(target.read_text().replace('"a": 1', '"a": 100'))
importlib.invalidate_caches()
importlib.reload(tables)
compiled.clear()
if who == "thread":
    starter.requests.put("user_1")
    value = starter.answers.get(timeout=60)
else:
    value = importlib.import_module(f"{pkg}.user_1").value
print(json.dumps({"reader": who, "tables.ROWS": tables.ROWS, "compiled": compiled, "value": value}))
'''
env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"} | {
    "DOEFF_HY_CODE_STORE": str(work / "store"), "PYTHONPYCACHEPREFIX": str(work / "pycache")}
def run(who):
    done = subprocess.run([python, "-c", CHILD, str(root), pkg, who], capture_output=True, text=True, env=env, timeout=120)
    if done.returncode:
        print(done.stderr[-2000:]); raise SystemExit(1)
    return json.loads(done.stdout.strip().splitlines()[-1])
original = (root / pkg / "tables.py").read_text()
print("thread_inherit_context:", subprocess.run([python, "-c", "import sys; print(sys.flags.thread_inherit_context)"], capture_output=True, text=True).stdout.strip())
for who in ("thread", "main"):
    (root / pkg / "tables.py").write_text(original)
    print(f"{who}: 温める", run("warm"))
    print(f"{who}: 読み込みの後に tables.py を 1 → 100 に書き換えて reload し、{who} が user_1 を読む:", run(who))
