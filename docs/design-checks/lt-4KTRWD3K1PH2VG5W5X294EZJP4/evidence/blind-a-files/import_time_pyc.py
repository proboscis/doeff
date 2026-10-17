"""job の import の時に書かれる .pyc が、hardlink で写した 2 つの root で同じ bytes になるか(保存先の 1 つの blob を分け合えるか)を測る。
共有の source は読むだけ。書くのは引数の W の下だけ。"""
import hashlib, importlib.util, marshal, os, subprocess, sys
from pathlib import Path

W, WT = Path(sys.argv[1]), Path(sys.argv[2])
base = W / "exp"
store, A, B = base / "store", base / "rootA" / "repo", base / "rootB" / "repo"
files = {"lazy/__init__.py": "", "lazy/plain.py": "VALUE = 41\n", "lazy/thing.hy": "(setv value (+ 1 41))\n"}
for rel, text in files.items():
    p = A / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text)
for rel in files:  # tree-placement と同じく、変わらない file は hardlink で写す(mtime も inode も同じ)
    q = B / rel; q.parent.mkdir(parents=True, exist_ok=True); os.link(A / rel, q)

child = ("import sys; sys.path.insert(0, sys.argv[1]); import hy; sys.pycache_prefix = None; "
         "import lazy.plain, lazy.thing; print(lazy.plain.VALUE, lazy.thing.value)")
for root in (A, B):  # 子の job と同じ: bytecode を書く設定・名指しの保存先(launch.hy が DOEFF_HY_CODE_STORE を継ぐ)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONDONTWRITEBYTECODE",)}
    env.update(DOEFF_HY_CODE_STORE=str(store), PYTHONPYCACHEPREFIX=str(base / "toolpyc"))
    r = subprocess.run([sys.executable, "-c", child, str(root)], cwd=root, env=env, capture_output=True, text=True, timeout=300)
    print("import", root.parent.name, "rc", r.returncode, r.stdout.strip(), r.stderr.strip()[-300:])

tag = sys.implementation.cache_tag
for name in ("plain", "thing"):
    pa, pb = (r / "lazy" / "__pycache__" / f"{name}.{tag}.pyc" for r in (A, B))
    da, db = pa.read_bytes(), pb.read_bytes()
    ca, cb = marshal.loads(da[16:]), marshal.loads(db[16:])
    print(f"{name}: same-bytes={da == db} same-inode={pa.stat().st_ino == pb.stat().st_ino} "
          f"headA={da[:16].hex()} headB={db[:16].hex()} sha-eq={hashlib.sha256(da).digest() == hashlib.sha256(db).digest()}")
    print(f"  co_filename A={ca.co_filename}\n  co_filename B={cb.co_filename}")

print("store .code entries:", sorted(str(p.relative_to(store)) for p in store.rglob("*.code")))
print("store .pyc blobs (今の import の口は置かない):", len(list(store.rglob("*.pyc"))))

# worker の版の code_store を path で読む(bytecode_pool の store-module-at と同じ読み方)
spec = importlib.util.spec_from_file_location("exp_code_store", WT / "packages/doeff-hy/src/doeff_hy_bytecode_guard/code_store.py")
cs = importlib.util.module_from_spec(spec); spec.loader.exec_module(cs)

# (1) 素直な拡張: import の口が今 set_data に渡す bytes を、そのまま公開の口 link_pyc へ渡す
naive = base / "naive-store"
for root in (A, B):
    for name in ("plain", "thing"):
        data = (root / "lazy" / "__pycache__" / f"{name}.{tag}.pyc").read_bytes()
        t = base / "naive-targets" / root.parent.name / f"{name}.pyc"
        print("naive link_pyc", root.parent.name, name, "->", cs.link_pyc(str(naive), data, str(t)))
print("naive store .pyc blobs:", len(list(naive.rglob("*.pyc"))), "(module 2 つ × root 2 つ — 分け合えば 2)")

# (2) 焼く道具(bytecode_pool の bake-one stored)の組み方: 頭 16 byte(checked hash)+ 保存先の entry の中身そのまま
sys.path.insert(0, str(WT / "packages/doeff-core-effects"))
import hy  # noqa
from doeff import run
from doeff_core_effects.python_bytecode import checked_hash_pyc
rule = base / "rule-store"
src = (A / "lazy/thing.hy").read_bytes()
key = cs.code_key(str(A / "lazy/thing.hy"), src, "lazy.thing", hy.__version__, sys.implementation.cache_tag or "", sys.flags.optimize)
for root in (A, B):
    found = cs.current_entry(str(store), key, lambda c: True)
    head = run(checked_hash_pyc(found.code, src))[:16]
    t = base / "rule-targets" / root.parent.name / "thing.pyc"
    print("rule link_pyc", root.parent.name, "->", cs.link_pyc(str(rule), head + found.data, str(t)))
print("rule store .pyc blobs:", len(list(rule.rglob("*.pyc"))), "(thing の 2 つの root が 1 つを分け合えば 1)")
