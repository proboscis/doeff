"""焼く道具(_foundation/bytecode_pool.hy)の pool の子が途中で殺されても、焼きは落ちず、答えの無い物だけを道具の親が焼き直す。

直す前は、子が 1 本殺されると pool が壊れ(concurrent.futures.process.BrokenProcessPool)、道具が 0 でない終わりで止まり、準備の全体が
「焼く道具の全体の報告の行を読めない」で落ちた(2026-11-02 18:56 JST の k3s-0 — memory 4 GiB の node の jobs の層の上限で子が OOM kill
され、新しい版の実行環境を作れず、配備の round が 45〜51 分待って戻した)。

模擬: 木の Hy の source 1 本の macro が、pool の子の中で初めて展開された時だけ自分の process を SIGKILL で止める(kernel の OOM kill と
同じ終わり)。並べる数 2 で焼き、道具が 0 で終わり、焼けない物が無く、全部の source の .pyc が在り、焼き直しを stderr で名乗る事を確かめる。
直す前の道具は BrokenProcessPool で 0 でない終わりになる。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: 焼く道具(本番の準備が worker 自身のコードから起こす file)と、worker の版の保存先の入口。
SRC = Path(__file__).resolve().parents[1] / "src" / "doeff_cluster"
TOOL = SRC / "_foundation" / "bytecode_pool.hy"
STORE_MODULE = (
    Path(__file__).resolve().parents[2] / "doeff-hy" / "src" / "doeff_hy_bytecode_guard" / "_code_store.py"
)

#: pool の子(親の process を持つ multiprocessing の子)の中で、印の file が無い時だけ印を置いて自分を止める macro。
KILLER_SOURCE = (
    "(defmacro die-once [] (import os signal multiprocessing)\n"
    "  (setv mark (get os.environ \"KILL_MARK\"))\n"
    "  (when (and (is-not (multiprocessing.parent-process) None) (not (os.path.exists mark)))\n"
    "    (with [f (open mark \"w\")] (.write f \"killed\\n\"))\n"
    "    (os.kill (os.getpid) signal.SIGKILL))\n"
    "  None)\n(die-once)\n(setv Z 3)\n"
)


def _plant(tree: Path) -> tuple[str, ...]:
    """殺す macro の Hy の source 1 本と Python の source 3 本の木を置き、焼く物の相対 path を返すため。"""
    package = tree / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    for name in ("a", "b", "c"):
        (package / f"{name}.py").write_text(f"{name.upper()} = 1\n")
    (package / "killer.hy").write_text(KILLER_SOURCE)
    return ("pkg/killer.hy", "pkg/__init__.py", "pkg/a.py", "pkg/b.py", "pkg/c.py")


def test_a_child_killed_mid_bake_does_not_fail_the_bake(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    rels = _plant(tree)
    mark = tmp_path / "killed"
    lines = "".join(f"{tree}\t{rel}\t{rel.removesuffix('.py').removesuffix('.hy').replace('/', '.')}\n" for rel in rels)
    argv = [sys.executable, "-m", "hy", str(TOOL), "--jobs", "2", "--store-module", str(STORE_MODULE), "--path", str(tree)]
    env = {**os.environ, "KILL_MARK": str(mark), "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run(argv, input=lines, capture_output=True, text=True, env=env, check=False)

    assert mark.exists(), "模擬の子が殺されていない(模擬が効いていない)"
    assert done.returncode == 0, done.stderr[-3000:]
    assert not [line for line in done.stdout.splitlines() if line.startswith("failed")], done.stdout
    for rel in rels:
        stem = Path(rel).stem
        assert list((tree / "pkg" / "__pycache__").glob(stem + ".*.pyc")), (rel, done.stderr[-2000:])
    assert "pool が壊れた" in done.stderr, done.stderr[-2000:]
