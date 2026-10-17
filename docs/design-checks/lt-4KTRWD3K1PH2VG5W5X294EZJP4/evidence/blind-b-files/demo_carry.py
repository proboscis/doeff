"""ShareBytecode(写し元の root の .pyc を新しい root へ hardlink で置く変更)の振る舞いを、本物の git・tar・os.link・焼く道具で観る。

mode = share   : 変更後の stage-trees と同じく、写した木(TREE-COPY)に ShareBytecode を出してから焼く
mode = control : 今の main と同じく、写した木をそのまま焼く(ShareBytecode を出さない)

筋書き:
  root-1 を展開して焼く(保存先に .code と .pyc が入る)
  -> 保存先の全部の file の時刻を 8 日前にする(= root-1 の後、保存先を引かない準備だけが 8 日続いた事の代わり)
  -> root-2 を root-1 から写して焼く
  -> worker の掃除(env_store の sweep-leftovers)を本物の file system で 1 回
  -> 写し元の無い root-3(別の env の root と同じ形 — 展開)を焼く
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import hy  # noqa: F401
import doeff_cluster
print("doeff_cluster from", doeff_cluster.__file__)
from doeff import do, run, with_handlers
from doeff_core_effects.handlers import reader, state
from doeff_core_effects.os_file import os_file_handler
from doeff_core_effects.os_process import subprocess_handler
from doeff_time import sync_time_handler

from doeff_cluster.worker.intent.env_prepare_model import TREE_COPY, TREE_EXPAND, MaterializeTree, TreeSource
from doeff_cluster.worker.protocol.env_translation import env_translation
from doeff_cluster.worker.protocol.env_store import EnvSettings, sweep_leftovers
from doeff_cluster.worker.core.env_upkeep import CODE_STORE_UNUSED_SECONDS

MODE, BASE, TOOL = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
PREFIX = BASE.parent / "pyc-prefix"   # 道具自身の import の .pyc(検の rig と同じ — 2 回目からは読むだけ)
HOOK = BASE.parent / "hook"
BASE.mkdir(parents=True)

HOOK_TEXT = """\
import os
_LOG = os.environ.get("BAKE_COMPILED_LOG")
if _LOG:
    import importlib.machinery as _m
    _orig = _m.SourceFileLoader.source_to_code
    def source_to_code(self, data, path, *a, **k):
        if isinstance(path, str) and path.endswith(".hy"):
            with open(_LOG, "a") as log:
                log.write(path + "\\n")
        return _orig(self, data, path, *a, **k)
    _m.SourceFileLoader.source_to_code = source_to_code
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def settings() -> dict:
    return {"runtime-env.state": str(BASE / "state"), "runtime-env.shared": str(BASE / "state"),
            "runtime-env.uv-cache": str(BASE / "uv-cache"), "runtime-env.repo-keys": {}, "runtime-env.code-prepare": "",
            "runtime-env.uv": "uv", "runtime-env.progress": "", "runtime-env.notes": "/dev/null"}


def translate(effect):
    @do
    def program():
        answer = yield effect
        return answer
    return run(with_handlers([state(), sync_time_handler(), reader(settings()), subprocess_handler, os_file_handler, env_translation],
                             program()))


def bake(tree: Path, store: Path, *, warm: bool = False) -> tuple[dict, list[str]]:
    log = tree.parent / "compiled.log"
    env = ["env", *(["-u", "PYTHONDONTWRITEBYTECODE"] if warm else ["PYTHONDONTWRITEBYTECODE=1"]),
           f"PYTHONPYCACHEPREFIX={PREFIX}", f"DOEFF_HY_CODE_STORE={store}", f"PYTHONPATH={HOOK}", f"BAKE_COMPILED_LOG={log}"]
    done = subprocess.run([*env, sys.executable, "-m", "hy", str(TOOL), "--revision", "r", "--tree", str(tree), "--roots", ".",
                           "--jobs", "1"], capture_output=True, text=True, cwd=tree, timeout=300, check=False)
    assert done.returncode == 0, done.stderr[-3000:]
    counts = {}
    for line in done.stderr.splitlines():
        if f"tree={tree} " in line:
            counts = {k: int(v) for k, v in (p.split("=", 1) for p in line.split() if "=" in p) if v.isdigit()}
    compiled = sorted(str(Path(p).relative_to(tree)) for p in (log.read_text().split() if log.exists() else [])
                      if Path(p).is_relative_to(tree))
    if log.exists():
        log.unlink()
    return counts, compiled


def pycs(tree: Path) -> dict[str, os.stat_result]:
    return {str(p.relative_to(tree)): p.stat() for p in sorted(tree.rglob("__pycache__/*.pyc"))}


def store_files(store: Path) -> dict[str, int]:
    kinds: dict[str, int] = {}
    for p in store.rglob("*"):
        if p.is_file():
            kinds[p.suffix] = kinds.get(p.suffix, 0) + 1
    return kinds


def inodes(*dirs: Path) -> dict[tuple[int, int], int]:
    found = {}
    for d in dirs:
        for p in d.rglob("*"):
            st = p.lstat()
            if stat.S_ISREG(st.st_mode):
                found[(st.st_dev, st.st_ino)] = st.st_blocks * 512
    return found


# --- repo と mirror ---------------------------------------------------------------------------
repo = BASE / "app-repo"
files = {
    "mac/__init__.py": "", "mac/macros.hy": "(defmacro answer [] (+ 1 1))\n",
    "use/__init__.py": "", "use/user.hy": "(require mac.macros [answer])\n(setv value (answer))\n",
    "other/__init__.py": "", "other/plain.hy": "(setv value 3)\n",
}
for i in range(20):
    files[f"bulk/m{i}.py"] = "".join(f"def f{j}(x):\n    return x + {i * 100 + j}\n" for j in range(60))
for rel, text in files.items():
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
git(repo, "init", "-q"); git(repo, "add", "-A"); git(repo, "commit", "-q", "-m", "v1")
v1 = git(repo, "rev-parse", "HEAD")
(repo / "bulk" / "m0.py").write_text((repo / "bulk" / "m0.py").read_text() + "VALUE = 2\n")
git(repo, "commit", "-q", "-am", "v2")
v2 = git(repo, "rev-parse", "HEAD")
mirror = BASE / "app.git"
subprocess.run(["git", "clone", "--bare", "-q", str(repo), str(mirror)], check=True, capture_output=True)
store = BASE / "store"

if not HOOK.exists():
    HOOK.mkdir()
    (HOOK / "sitecustomize.py").write_text(HOOK_TEXT)

# --- root-1: 展開して焼く ----------------------------------------------------------------------
r1 = BASE / "root-1" / "app"
assert translate(MaterializeTree(str(mirror), v1, str(r1), None)).how == TREE_EXPAND
c1, k1 = bake(r1, store, warm=not PREFIX.exists())
print(f"[{MODE}] root-1 bake: {c1}  compiled(hy)={k1}")
print(f"[{MODE}] store after root-1: {store_files(store)}")

# --- 8 日たった事にする(保存先の file と dir の時刻を戻す)------------------------------------------
past = time.time() - CODE_STORE_UNUSED_SECONDS - 24 * 3600
for p in sorted(store.rglob("*"), key=lambda p: -len(p.parts)):
    os.utime(p, (past, past))

# --- root-2: root-1 から写して焼く ---------------------------------------------------------------
r2 = BASE / "root-2" / "app"
placed = translate(MaterializeTree(str(mirror), v2, str(r2), TreeSource(dir=str(r1), commit=v1)))
assert (placed.how, placed.reason) == (TREE_COPY, ""), placed
if MODE == "share":
    from doeff_cluster.worker.intent.env_prepare_model import ShareBytecode
    shared = translate(ShareBytecode(str(r1), str(r2)))
    print(f"[{MODE}] ShareBytecode placed {shared} .pyc from root-1")
c2, k2 = bake(r2, store)
print(f"[{MODE}] root-2 bake: {c2}  compiled(hy)={k2}")
old = [p for p in store.rglob("*") if p.is_file() and p.stat().st_mtime < past + 60]
print(f"[{MODE}] store files still dated 8 days ago after root-2 (not marked used): {len(old)} / "
      f"{sum(1 for p in store.rglob('*') if p.is_file())}")

# --- worker の掃除(env_store の sweep-leftovers を本物の file system で)-------------------------------
st = BASE / "state"
(st / "roots").mkdir(parents=True)
es = EnvSettings(state=str(st), uv_cache=str(st / "uv-cache"), hy_command="hy", platform="test", code_prepare="x",
                 roots_cap_bytes=2 ** 62, code_store=str(store))
removed = run(with_handlers([os_file_handler], sweep_leftovers(es, int(time.time() * 1000))))
print(f"[{MODE}] sweep removed {len(removed)} paths under the store; store now: {store_files(store)}")
print(f"[{MODE}] root-2 .pyc still present (tree names survive): {len(pycs(r2))}")

# --- root-3: 写し元の無い root(別の env と同じ形 — 展開)を焼く -------------------------------------------
r3 = BASE / "root-3" / "app"
assert translate(MaterializeTree(str(mirror), v2, str(r3), None)).how == TREE_EXPAND
before = inodes(r2.parent, store)
c3, k3 = bake(r3, store)
print(f"[{MODE}] root-3 bake: {c3}  compiled(hy)={k3}")
p2, p3 = pycs(r2), pycs(r3)
same = sum(1 for rel, s in p3.items() if rel in p2 and p2[rel].st_ino == s.st_ino)
print(f"[{MODE}] root-3 .pyc sharing one file with root-2: {same} / {len(p3)}")
written = sum(size for ino, size in inodes(r3.parent, store).items() if ino not in before)
pyc_written = sum(s.st_blocks * 512 for rel, s in p3.items() if (s.st_dev, s.st_ino) not in before)
print(f"[{MODE}] root-3 new bytes (root-3 + store, new inodes): {written}; of which .pyc: {pyc_written}")
