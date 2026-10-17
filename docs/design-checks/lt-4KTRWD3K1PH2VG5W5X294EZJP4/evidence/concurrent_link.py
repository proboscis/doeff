"""S5(並行): 8 つの process が同時に、同じ中身の .pyc を保存先経由で 50 の木へ置く(link_pyc)。その後 1 つの木の .pyc を別の中身で置き直す。
観る物: 全部の木の中身が同じ・保存先の .pyc の blob が中身ごとに 1 つ・一時の file(.tmp・.link)が残らない・置き直しが他の木と保存先を変えない。
使い方: uv run --no-sync python concurrent_link.py <空の dir>"""
import multiprocessing
import os
import sys
from pathlib import Path

from doeff_hy_bytecode_guard import code_store

BASE = Path(sys.argv[1])
STORE = str(BASE / "store")
PYC = b"\x01" * 16 + b"shared body" * 50


def worker(n: int) -> list:
    problems = []
    for t in range(50):
        problem = code_store.link_pyc(STORE, PYC, str(BASE / f"tree-{t}" / "__pycache__" / f"m{n}.pyc"))
        if problem is not None:
            problems.append(problem)
    return problems


if __name__ == "__main__":
    with multiprocessing.get_context("fork").Pool(8) as pool:
        problems = [p for ps in pool.map(worker, range(8)) for p in ps]
    targets = sorted(BASE.glob("tree-*/__pycache__/*.pyc"))
    blobs = sorted(Path(STORE).rglob("*.pyc"))
    leftovers = sorted(str(p) for p in BASE.rglob("*") if p.name.endswith((".tmp", ".link")))
    print("problems:", problems)
    same = all(p.read_bytes() == PYC for p in targets)
    print("targets:", len(targets), "all same bytes:", same)
    print("store .pyc blobs:", len(blobs), "nlink:", [b.stat().st_nlink for b in blobs])
    print("leftover temporaries:", leftovers)
    other = PYC + b" changed"
    print("replace tree-0/m0:", code_store.link_pyc(STORE, other, str(BASE / "tree-0" / "__pycache__" / "m0.pyc")))
    print("tree-1/m0 still the shared bytes:", (BASE / "tree-1" / "__pycache__" / "m0.pyc").read_bytes() == PYC)
    print("old blob still the shared bytes:", blobs[0].read_bytes() == PYC)
    ok = (not problems and len(targets) == 400 and same and len(blobs) == 1 and not leftovers
          and (BASE / "tree-1" / "__pycache__" / "m0.pyc").read_bytes() == PYC and blobs[0].read_bytes() == PYC)
    sys.exit(0 if ok else 1)
