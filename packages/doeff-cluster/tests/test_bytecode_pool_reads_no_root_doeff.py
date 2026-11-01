"""焼く道具(_foundation/bytecode_pool.hy)は、準備する root の venv の doeff の package から名を import しない(ADR-DOE-BUILD-002 R7)。

直す前は、道具が root の doeff-hy の guard(doeff_hy_bytecode_guard の入口・後に _loader_hooks)と python_bytecode を module の名で
import したので、worker の版と root の版の doeff が組になった。名の置き場を内側へ移した doeff(2026-10-29)の root では、それより古い
worker の準備が import の誤りで必ず落ち、新しい版の root を 1 時間置けなかった(2026-11-01)。

模擬: 道具を、root の guard の口の持ち主の module が今と違う名(renamed_guard_hooks)で在り、道具の file の import の文が doeff の
package の名(doeff・doeff_core_effects・doeff_hy・doeff_hy_bytecode_guard・doeff_cluster ほか doeff で始まる名)を引くと
ImportError になる process で起こす。macro を木の中の別の module から require する Hy の source と Python の source を焼き、
1 回目は焼けて(failed が無く .pyc が在る)、2 回目は在る .pyc が今の macro に合うので全部を残し(reused — guard の照らしが効く)、
.pyc を消した 3 回目は保存先の code から置く(stored)事を確かめる。直す前の道具は 1 回目の import の文で落ちる。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

#: 焼く道具(本番の準備が worker 自身のコードから起こす file)と、worker の版の保存先の入口。
SRC = Path(__file__).resolve().parents[1] / "src" / "doeff_cluster"
TOOL = SRC / "_foundation" / "bytecode_pool.hy"
STORE_MODULE = (
    Path(__file__).resolve().parents[2] / "doeff-hy" / "src" / "doeff_hy_bytecode_guard" / "_code_store.py"
)

#: 道具を起こす前に、root の guard の口の持ち主の名を替え、道具の file からの doeff の名の import を断る起動の script。
LAUNCH = r"""
import builtins, importlib.machinery, os, runpy, sys
tool = os.path.realpath(sys.argv[1])
loader = importlib.machinery.SourceFileLoader
owner = sys.modules[loader.get_code.__module__]
for hook in ("get_code", "source_to_code"):
    getattr(loader, hook).__module__ = "renamed_guard_hooks"
sys.modules["renamed_guard_hooks"] = owner
original_import = builtins.__import__

def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    caller = (globals or {}).get("__file__")
    if caller and os.path.realpath(caller) == tool and level == 0 and name.split(".")[0].startswith("doeff"):
        raise ImportError(f"the bake tool imported {name} from the root's doeff", name=name)
    return original_import(name, globals, locals, fromlist, level)

builtins.__import__ = guarded_import
sys.argv = ["hy", *sys.argv[1:]]
runpy.run_module("hy", run_name="__main__", alter_sys=True)
"""


def _plant(tree: Path) -> tuple[str, ...]:
    """macro を木の中の別の module から require する Hy の source と Python の source の木を置き、焼く物の相対 path を返すため。"""
    package = tree / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "plain.py").write_text("Y = 2\n")
    (package / "macros.hy").write_text("(defmacro twice [x] `(* 2 ~x))\n")
    (package / "user.hy").write_text("(require pkg.macros [twice])\n(setv X (twice 3))\n")
    return ("pkg/__init__.py", "pkg/plain.py", "pkg/macros.hy", "pkg/user.hy")


def _bake(tree: Path, store: Path, rels: tuple[str, ...]) -> list[str]:
    """模擬の process で道具を 1 回起こし、結果の行を返すため(道具が 0 でない終わりなら落とす)。"""
    lines = "".join(f"{tree}\t{rel}\t{rel.removesuffix('.py').removesuffix('.hy').replace('/', '.')}\n" for rel in rels)
    argv = [sys.executable, "-c", LAUNCH, str(TOOL), "--jobs", "2", "--store-module", str(STORE_MODULE),
            "--store", str(store), "--path", str(tree)]
    done = subprocess.run(argv, input=lines, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr[-3000:]
    return done.stdout.splitlines()


def test_the_bake_tool_works_where_the_roots_guard_has_another_name_and_doeff_names_are_refused(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    store = tmp_path / "store"
    rels = _plant(tree)

    first = _bake(tree, store, rels)
    assert not [line for line in first if line.startswith("failed")], first
    pycs = {rel: tree / "pkg" / "__pycache__" for rel in rels}
    for rel, cache in pycs.items():
        assert list(cache.glob(Path(rel).stem + ".*.pyc")), (rel, first)

    second = _bake(tree, store, rels)
    assert sorted(line for line in second if line.startswith("reused")) == sorted(f"reused\t{tree}\t{rel}" for rel in rels), second

    for pyc in (tree / "pkg" / "__pycache__").glob("*.pyc"):
        pyc.unlink()
    third = _bake(tree, store, rels)
    assert sorted(line for line in third if line.startswith("stored")) == sorted(f"stored\t{tree}\t{rel}" for rel in rels), third
