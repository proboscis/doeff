import importlib.util, sys
from pathlib import Path
for mode in ("control", "share"):
    tree = Path(sys.argv[1]) / mode / "root-2" / "app"
    stale, total = [], 0
    for pyc in sorted(tree.rglob("__pycache__/*.pyc")):
        stem = pyc.name.split(".")[0]
        src = next((pyc.parent.parent / (stem + s) for s in (".py", ".hy") if (pyc.parent.parent / (stem + s)).exists()), None)
        data = pyc.read_bytes()
        flags = int.from_bytes(data[4:8], "little")
        total += 1
        if flags & 1 and data[8:16] != importlib.util.source_hash(src.read_bytes()):
            stale.append(str(pyc.relative_to(tree)))
    m0 = next(tree.glob("bulk/__pycache__/m0.*.pyc"))
    print(f"[{mode}] root-2 .pyc whose recorded source hash != current source: {stale} (of {total}); m0.pyc nlink={m0.stat().st_nlink}")
