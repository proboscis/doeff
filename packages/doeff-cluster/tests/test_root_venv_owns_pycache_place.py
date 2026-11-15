"""置き場を使う root の venv の python は、起こし方と継いだ環境に関わらず、root の木の中へ .pyc を書かない(root の .pyc の置き場は venv が持つ)。

直す前は、root の .pyc の置き場を process ごとの環境変数 PYTHONPYCACHEPREFIX で運んでいた。準備の後に、-I で起こす待ちの子の見張り
(worker/entry/warm_watch の watch_argv — -I は PYTHON* の環境変数を読まない)・環境を組み直して起こす子・置き場の無い起こし口の子が
木の中の __pycache__ へ書き(2026-11-15 の本番の root 1 つで 3,432 個)、別の root の子は継いだ置き場へ書いた。直した後は、準備が root の
venv の site-packages に起動の口(_foundation/pycache_scope.py)と置き場の path の file を置き、口が起動の .pth の段でその file から
sys.pycache_prefix を置く — .pth は -I でも読まれる。

台 = 本物の venv(この test の interpreter の `python -m venv --without-pip`)に、準備の WriteImportRoots・CompileTrees と同じ名と中身で
口と .pth と置き場の file を置き、木の source を bytecode を書く設定で import する(PYTHONDONTWRITEBYTECODE を外す — 本番の子と同じく
書く)。
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from doeff_cluster._foundation import pycache_scope
from doeff_cluster._worker.intent.env_prepare_model import (
    PYCACHE_SCOPE_LINE,
    PYCACHE_SCOPE_MODULE,
    PYCACHE_SCOPE_PTH,
)

#: 置き場の口の source(本番の準備が venv へ写す物 — env_translation の PYCACHE-SCOPE-SOURCE と同じ file)。
SCOPE_SOURCE = Path(pycache_scope.__file__).read_text()

#: 木の module を import する子(木を sys.path の頭に足す — 本番は import の根の .pth)。孫 = 同じ venv の python を -I で起こし直す
#: (待ちの子が見張りを起こす形)。
_IMPORT = """\
import subprocess, sys
sys.path.insert(0, sys.argv[1])
import pkg.first
if len(sys.argv) > 2:
    subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import pkg.second", sys.argv[1]],
                   check=True)
"""


@dataclass(frozen=True)
class _Root:
    """台の root 1 つ: tree = repo の木・place = root の .pyc の置き場(ROOT-PYCACHE の位置)・python = root の venv の python。"""

    tree: Path
    place: Path
    python: Path


def _root(base: Path, *, placed: bool) -> _Root:
    """root を 1 つ作る: 木に 2 つの module を置き、venv を作って口と .pth と置き場の file を置く(placed = 偽は空の file — 焼く木の
    無かった root)。"""
    tree, place, venv = base / "repo", base / ".doeff-pycache", base / ".venv"
    (tree / "pkg").mkdir(parents=True)
    (tree / "pkg" / "__init__.py").write_text("")
    (tree / "pkg" / "first.py").write_text("value = 1\n")
    (tree / "pkg" / "second.py").write_text("value = 2\n")
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True, timeout=120)
    site = next(venv.glob("lib/python*/site-packages"))
    (site / f"{PYCACHE_SCOPE_MODULE}.py").write_text(SCOPE_SOURCE)
    (site / PYCACHE_SCOPE_PTH).write_text(PYCACHE_SCOPE_LINE)
    (site / pycache_scope.PREFIX_FILE).write_text(f"{place}\n" if placed else "")
    return _Root(tree=tree, place=place, python=venv / "bin" / "python")


def _environ(root: _Root, **extra: str) -> dict[str, str]:
    """子の環境: job の子と同じく root の venv の bin を PATH に・VIRTUAL_ENV に venv を置き(launch の uv-run-environ の形)、extra を
    足す。この test の環境は継がない(PYTHONDONTWRITEBYTECODE の無い — bytecode を書く子にする)。"""
    venv = root.python.parent.parent
    return {"PATH": str(venv / "bin"), "VIRTUAL_ENV": str(venv), **extra}


def _run(argv: list[str], env: dict[str, str]) -> None:
    """子を起こして終わりを待つ(落ちたら stderr を名乗る)。"""
    completed = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=120, check=False)
    assert completed.returncode == 0, completed.stderr


def _tree_pycache(tree: Path) -> list[str]:
    """木の下の __pycache__ の dir と .pyc(root の木からの相対 path)。"""
    return sorted(str(p.relative_to(tree)) for p in tree.rglob("*") if p.name == "__pycache__" or p.suffix == ".pyc")


def _foreign(other: Path) -> list[str]:
    """継いだ別の root の置き場に書かれた .pyc のうち、interpreter の起動の段(site の前 — venv の .pth より先)に読む encodings の外の物。
    encodings は site の前に import されるので venv の口では外せない(PYTHONPYCACHEPREFIX を継いだ時だけ — worker は子へ渡さない)。"""
    return sorted(str(p) for p in other.rglob("*.pyc") if "encodings" not in p.parts) if other.exists() else []


def _placed(place: Path, tree: Path) -> list[str]:
    """置き場の下の、木の pkg の .pyc の名(PYTHONPYCACHEPREFIX と同じ並べ方 — 木の dir の絶対 path を置き場の下に写した dir)。"""
    mirrored = place / str(tree / "pkg").lstrip("/")
    return sorted(p.name.split(".")[0] for p in mirrored.glob("*.pyc")) if mirrored.is_dir() else []


@pytest.mark.parametrize(
    ("way", "flags", "environ"),
    [
        ("job", [], {}),
        ("watch", ["-I"], {}),
        ("rebuilt-environment", [], {"__clean__": "1"}),
        ("inherited-other-prefix", [], {"PYTHONPYCACHEPREFIX": "__other__"}),
        ("inherited-other-prefix-isolated", ["-I"], {"PYTHONPYCACHEPREFIX": "__other__"}),
    ],
)
def test_a_root_venv_python_writes_the_tree_pyc_only_into_its_place(
    tmp_path: Path, way: str, flags: list[str], environ: dict[str, str],
) -> None:
    """受入: 置き場を使う root の venv の python を、job の形・見張りの -I・環境を組み直した形(PATH だけ)・別の root の置き場を継いだ形で
    起こし、孫も -I で起こす。木の下に __pycache__ が 1 つも無く、木の module の .pyc は自分の root の置き場に在り、継いだ別の root の
    置き場には書かない(interpreter が site の前に読む encodings の外 — _foreign)。"""
    root = _root(tmp_path / "root", placed=True)
    other = tmp_path / "other-root" / ".doeff-pycache"
    if "__clean__" in environ:
        env = {"PATH": "/usr/bin:/bin"}
    else:
        env = _environ(root, **{k: str(other) if v == "__other__" else v for k, v in environ.items()})
    _run([str(root.python), *flags, "-c", _IMPORT, str(root.tree), "grandchild"], env)
    assert _tree_pycache(root.tree) == [], way
    assert _placed(root.place, root.tree) == ["__init__", "first", "second"], way
    assert _foreign(other) == [], way


def test_b_a_root_without_a_place_does_not_write_into_the_inherited_place(tmp_path: Path) -> None:
    """置き場の file が空の root(焼く木の無かった root)の venv の python は、job の環境から継いだ別の root の PYTHONPYCACHEPREFIX の
    置き場へ書かない(木の隣の __pycache__ を使う — 置き場を使わない root の前からの形。encodings の外 — _foreign)。"""
    root = _root(tmp_path / "root", placed=False)
    other = tmp_path / "other-root" / ".doeff-pycache"
    _run([str(root.python), "-c", _IMPORT, str(root.tree), "grandchild"], _environ(root, PYTHONPYCACHEPREFIX=str(other)))
    assert _foreign(other) == []
    assert "pkg/__pycache__" in _tree_pycache(root.tree)


def test_c_the_venv_python_keeps_the_standard_library_beside_its_source(tmp_path: Path) -> None:
    """口は置き場を使う root の venv でも、標準 library の .pyc を隣の __pycache__ から読む(置き場に標準 library を書かない)。"""
    root = _root(tmp_path / "root", placed=True)
    _run([str(root.python), "-I", "-c", "import json, sys; sys.path.insert(0, sys.argv[1]); import pkg.first", str(root.tree)],
         _environ(root))
    assert not (root.place / str(Path(os.__file__).parent).lstrip("/")).exists()
