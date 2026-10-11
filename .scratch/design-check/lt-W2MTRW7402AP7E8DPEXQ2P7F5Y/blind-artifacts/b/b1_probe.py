"""B1 の探り(盲検 B の失敗例の確かめ)— 別の top の package から import した素の値を macro が返す時、その file を替えた
次の process が記録を古いと判じるか。

使い方: uv run --project <doeff の写し> python b1_probe.py <空の作業 dir>
答え: 各段の JSON の行と、最後に要約の 1 行。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

CHILD = """\
import importlib.machinery, json, sys
from pathlib import Path
root, stored, mode = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
sys.path.insert(0, root)
import hy
import doeff_hy
from doeff_hy_bytecode_guard import macro_use, record_from_json, record_is_current_here, record_to_json
from doeff_hy_bytecode_guard import records, source_to_code_as_import
walked = {"count": 0}
original = macro_use.record_is_current
def counting(*args, **kwargs):
    walked["count"] += 1
    return original(*args, **kwargs)
macro_use.record_is_current = counting
answer = {}
if mode == "record":
    path = f"{root}/b1probe_a/user.hy"
    loader = importlib.machinery.SourceFileLoader("b1probe_a.user", path)
    record = records.record_of(source_to_code_as_import(loader, open(path, "rb").read(), path))
    stored.write_text(json.dumps(record_to_json(record)))
    answer["record_files"] = [row.module for row in record.files]
    answer["current"] = record_is_current_here(record)
elif mode == "check":
    answer["current"] = record_is_current_here(record_from_json(json.loads(stored.read_text())))
else:
    import b1probe_a.user as user
    answer["value"] = user.value
answer["walked"] = walked["count"]
print(json.dumps(answer))
"""

FILES = {
    "b1probe_a/__init__.py": "",
    "b1probe_a/macros.hy": "(import b1probe_b.limits [LIMIT])\n(import b1probe_a.local [STEP])\n"
    "(defmacro limited [] LIMIT)\n(defmacro stepped [] STEP)\n",
    "b1probe_a/local.hy": "(setv STEP 5)\n",
    "b1probe_a/user.hy": "(require b1probe_a.macros [limited stepped])\n(setv value [(limited) (stepped)])\n",
    "b1probe_b/__init__.py": "",
    "b1probe_b/limits.hy": "(setv LIMIT 3)\n",
}


def prepare(work: Path) -> tuple[Path, dict[str, str]]:
    root = work / "root"
    for name, text in FILES.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    (work / "store").mkdir()
    environment = dict(os.environ)
    environment["DOEFF_HY_CODE_STORE"] = str(work / "store")
    environment["PYTHONPYCACHEPREFIX"] = str(work / "pycache")
    return root, environment


def run(root: Path, environment: dict[str, str], stored: Path, mode: str) -> dict[str, object]:
    done = subprocess.run(
        [sys.executable, "-c", CHILD, str(root), str(stored), mode],
        capture_output=True, text=True, timeout=120, check=False, env=environment,
    )
    if done.returncode != 0:
        raise SystemExit(done.stderr)
    return json.loads(done.stdout.strip().splitlines()[-1])


def replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text and len(old) != len(new)
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def verdicts(work: Path) -> int:
    return len(list((work / "store").glob("*/*.verdict")))


def main() -> None:
    work = Path(sys.argv[1])
    summary: dict[str, object] = {}
    # 段 A: 記録を作る process → 照らすだけの process(何も変えない)→ 別の top の package の値を替えて照らす process。
    a = work / "a"
    root, environment = prepare(a)
    stored = a / "record.json"
    steps = [("A1 record", run(root, environment, stored, "record"))]
    steps.append(("A2 check(変更なし)", run(root, environment, stored, "check")))
    replace(root / "b1probe_b/limits.hy", "(setv LIMIT 3)", "(setv LIMIT 30)")
    steps.append(("A3 check(別 package の LIMIT 3→30)", run(root, environment, stored, "check")))
    for label, answer in steps:
        print(label, json.dumps(answer, ensure_ascii=False))
    print("A の置き場の .verdict の数:", verdicts(a))
    summary["A3_current"] = steps[-1][1]["current"]
    # 段 B: 普通の import(作業木の .pyc を読む口)。1 度目は compile、2 度目は .pyc の記録を照らす、値を替えて 3 度目。
    b = work / "b"
    root, environment = prepare(b)
    stored = b / "unused.json"
    steps = [("B1 import", run(root, environment, stored, "import"))]
    steps.append(("B2 import(変更なし)", run(root, environment, stored, "import")))
    replace(root / "b1probe_b/limits.hy", "(setv LIMIT 3)", "(setv LIMIT 30)")
    steps.append(("B3 import(別 package の LIMIT 3→30)", run(root, environment, stored, "import")))
    for label, answer in steps:
        print(label, json.dumps(answer, ensure_ascii=False))
    print("B の置き場の .verdict の数:", verdicts(b))
    summary["B3_value"] = steps[-1][1]["value"]
    # 段 C(対照): 同じ top の package の値(b1probe_a.local の STEP)を替える — 鍵が覆う形。
    c = work / "c"
    root, environment = prepare(c)
    stored = c / "record.json"
    run(root, environment, stored, "record")
    run(root, environment, stored, "check")
    replace(root / "b1probe_a/local.hy", "(setv STEP 5)", "(setv STEP 50)")
    answer = run(root, environment, stored, "check")
    print("C3 check(同じ package の STEP 5→50)", json.dumps(answer, ensure_ascii=False))
    summary["C3_current"] = answer["current"]
    print("要約", json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
