"""実行環境の root の木の .pyc と焼きの記録は、木の外の root の置き場(PYTHONPYCACHEPREFIX の並べ方)に置く(ADR-DOE-BUILD-002 R3d)。

直す前は、準備の道具(worker/entry/code_prepare.hy)が .pyc を source の隣の __pycache__ に、閉包の表(.doeff-closure-table.json)と木の
完成の印(.doeff-code-ready.json)を木の根に書いた。新しい root のたびに木の中へ .pyc の hardlink を張り(家の server の root 1 つの inode の
表の block の 17%)、木を読み専用で置けなかった。直した後は --pycache-prefix の在る回は木に何も書かず、子は root の venv の起動の口
(_foundation/pycache_scope.py — venv の site-packages の置き場の file を読む)で同じ置き場を読み、source を compile し直さない。口は
標準 library と venv の package を隣の __pycache__ に残す(置き場で探すと、起動のたびに compile し直す)。

台 = test_code_prepare_inherits.py と同じ形の小さな Hy と Python の木(macro の定義元・使う側・葉と、全部を import する入口 app.main)。
compile の観測は同じく sitecustomize の数え(道具と子の外から数える — 木の中の source が source_to_code を通った数)。速さのため、道具自身の
import の .pyc は 1 度だけ置き場とは別の dir(rig.own)に書かせる。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from doeff_cluster._foundation import pycache_scope
from doeff_cluster._worker.intent.env_prepare_model import PYCACHE_SCOPE_MODULE

#: 準備の道具(本番の準備の処理ステージが起こす script と同じ file)と、root の venv の起動の口。
TOOL = Path(__file__).resolve().parents[1] / "src" / "doeff_cluster" / "_worker" / "entry" / "code_prepare.hy"
SCOPE = Path(__file__).resolve().parents[1] / "src" / "doeff_cluster" / "_foundation"

#: compile の数え(BAKE_COMPILED_LOG の file へ、source_to_code を通った source の path を 1 行ずつ)と、在れば root の venv の起動の口
#: (本番は venv の .pth が入れる — 台は sitecustomize で、venv の site-packages を真似た dir SCOPE_DIR に写した口の install を呼ぶ)。
_HOOK = """\
import os

if os.environ.get("SCOPE_DIR"):
    import sys
    sys.path.insert(0, os.environ["SCOPE_DIR"])
    import _doeff_pycache_scope
    _doeff_pycache_scope.install()
    del sys.path[0]

_LOG = os.environ.get("BAKE_COMPILED_LOG")
if _LOG:
    import importlib.machinery as _machinery

    _original = _machinery.SourceFileLoader.source_to_code

    def source_to_code(self, data, path, *args, **kwargs):
        if isinstance(path, str):
            with open(_LOG, "a") as log:
                log.write(path + "\\n")
        return _original(self, data, path, *args, **kwargs)

    _machinery.SourceFileLoader.source_to_code = source_to_code
"""

#: 読む側 — 子の job と同じく、用意した木を import して値を読み、木の module の .pyc の在り処(__cached__)を名乗る。
_IMPORT = """\
import sys
sys.path.insert(0, sys.argv[1])
import hy
import app.main
import use.user
print(use.user.__cached__)
print(app.main.total)
"""

#: 起動の口の効き方 — 標準 library の module と木の source の .pyc の path を名乗る。
_WHERE = """\
import importlib.util, json, sys
print(importlib.util.cache_from_source(json.__file__))
print(importlib.util.cache_from_source(sys.argv[1]))
"""


def _files(salt: str) -> dict[str, str]:
    """台の file の中身(salt = 件ごとに中身を変える註の行)。"""
    return {
        "app/__init__.py": f"# {salt}\n",
        "app/main.py": f"# {salt}\nimport use.user\nimport leaf.plain\ntotal = use.user.value + leaf.plain.value\n",
        "mac/__init__.py": f"# {salt}\n",
        "mac/macros.hy": f"; {salt}\n(defmacro answer [] (+ 1 1))\n",
        "use/__init__.py": f"# {salt}\n",
        "use/user.hy": f"; {salt}\n(require mac.macros [answer])\n(setv value (answer))\n",
        "leaf/__init__.py": f"# {salt}\n",
        "leaf/plain.hy": f"; {salt}\n(setv value 100)\n",
    }


def _write(root: Path, files: dict[str, str]) -> None:
    """file を置く。"""
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _copy_tree(source: Path, dest: Path) -> None:
    """source の木の source を dest へ hardlink で写す(__pycache__ と隠し file は写さない — 準備の MaterializeTree と同じ形)。"""
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source)
        if path.is_dir() or "__pycache__" in rel.parts or any(part.startswith(".") for part in rel.parts):
            continue
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        os.link(path, dest / rel)


def _mirror(prefix: Path, tree: Path) -> Path:
    """置き場の下の、木の dir を写した dir(bake_plan の records-dir と同じ並べ方)。"""
    return prefix / str(tree).lstrip("/")


def _assert_carried_unchecked(stderr: str) -> None:
    """焼きの道具の引き継ぎの行が、写し元から置いた .pyc を全部照らさずに残した事を名乗る(carried = trusted > 0・checked = 0)。"""
    found = re.search(r"carried=(\d+) trusted=(\d+) checked=(\d+)", stderr)
    assert found is not None, stderr
    carried, trusted, checked = map(int, found.groups())
    assert carried > 0 and carried == trusted and checked == 0, found.group(0)


def _tree_writes(tree: Path) -> list[str]:
    """木の中の、source でない物(__pycache__ の下の物と隠し file — 焼きの記録)の相対 path。"""
    return sorted(
        str(p.relative_to(tree)) for p in tree.rglob("*")
        if "__pycache__" in p.relative_to(tree).parts or p.name.startswith(".")
    )


@dataclass(frozen=True)
class _Rig:
    """件をまたいで使う物: hook = 数えと起動の口の sitecustomize の dir・own = 道具自身の import の .pyc の dir(checkout の外)。"""

    hook: Path
    own: Path


def _site(base: Path, prefix: Path | None) -> Path:
    """root の venv の site-packages を真似た dir に、準備と同じ名で起動の口と置き場の file を置く(prefix = None は空の file —
    置き場を使わない venv)。"""
    site = base / "site"
    site.mkdir(parents=True, exist_ok=True)
    (site / f"{PYCACHE_SCOPE_MODULE}.py").write_text((SCOPE / "pycache_scope.py").read_text())
    (site / pycache_scope.PREFIX_FILE).write_text("" if prefix is None else f"{prefix}\n")
    return site


def _env(rig: _Rig, prefix: Path, compiled: Path, *, site: Path | None = None, write: bool = False) -> list[str]:
    """道具と子に渡す環境。site が無い = 焼きの道具(本番は --pycache-prefix の引数 — 台は道具自身の import の .pyc の置き場を
    PYTHONPYCACHEPREFIX で別の dir に向ける)。site が在る = root の venv の子(起動の口が site の file から置き場を読む — 環境の
    PYTHONPYCACHEPREFIX は別の root の置き場 prefix を継いだ形にして、口が上書きする事も見る)。"""
    return [
        "env", *(["-u", "PYTHONDONTWRITEBYTECODE"] if write else ["PYTHONDONTWRITEBYTECODE=1"]),
        f"PYTHONPYCACHEPREFIX={prefix}", f"PYTHONPATH={rig.hook}", f"BAKE_COMPILED_LOG={compiled}",
        *([f"SCOPE_DIR={site}"] if site is not None else []),
    ]


def _bake(tree: Path, store: Path, rig: _Rig, prefix: Path | None, *, inherit: Path | None = None,
          inherit_prefix: Path | None = None) -> str:
    """本番の準備と同じ引数の形で道具を起こす(--entries app.main・prefix = --pycache-prefix・None は置き場を使わない前の形)。答え = stderr。"""
    compiled = tree.parent / f"{tree.name}.bake.log"
    completed = subprocess.run(
        [
            *_env(rig, rig.own, compiled), f"DOEFF_HY_CODE_STORE={store}",
            sys.executable, "-m", "hy", str(TOOL), "--revision", "r", "--tree", str(tree), "--roots", ".", "--jobs", "1",
            "--entries", "app.main",
            *(["--pycache-prefix", str(prefix)] if prefix is not None else []),
            *(["--inherit", str(inherit)] if inherit is not None else []),
            *(["--inherit-pycache-prefix", str(inherit_prefix)] if inherit_prefix is not None else []),
        ],
        capture_output=True, text=True, cwd=tree, timeout=180, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stderr


@dataclass(frozen=True)
class _ChildRead:
    """子の読みの答え: compiled = compile した木の中の source の相対 path(名の順)・cached = use.user の __cached__・total = app.main の
    合計。"""

    compiled: list[str]
    cached: str
    total: int


def _child(tree: Path, prefix: Path, rig: _Rig) -> _ChildRead:
    """用意した木を子の job と同じく import する(置き場 = root の venv の口の file・環境の PYTHONPYCACHEPREFIX は別の root の置き場・
    bytecode を書かない)。"""
    compiled = tree.parent / f"{tree.name}.child.log"
    site = _site(tree.parent / f"{tree.name}.venv", prefix)
    completed = subprocess.run(
        [*_env(rig, tree.parent / "other-root-pycache", compiled, site=site), sys.executable, "-c", _IMPORT, str(tree)],
        capture_output=True, text=True, cwd=tree, timeout=180, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    lines = compiled.read_text().split() if compiled.exists() else []
    names = sorted({str(Path(line).relative_to(tree)) for line in lines if Path(line).is_relative_to(tree)})
    cached, total = completed.stdout.split()[-2:]
    return _ChildRead(compiled=names, cached=cached, total=int(total))


@pytest.fixture(scope="module")
def rig(tmp_path_factory: pytest.TempPathFactory) -> _Rig:
    """hook を置き、道具を 1 度だけ .pyc を書く設定で起こして、道具自身の import の .pyc を own の dir に作る。"""
    base = tmp_path_factory.mktemp("rig")
    hook, own, warm = base / "hook", base / "own", base / "warm"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text(_HOOK)
    _write(warm, _files("warm"))
    completed = subprocess.run(
        [*_env(_Rig(hook, own), own, base / "warm.log", write=True), f"DOEFF_HY_CODE_STORE={base / 'store'}",
         sys.executable, "-m", "hy", str(TOOL), "--revision", "r", "--tree", str(warm), "--roots", ".", "--jobs", "1"],
        capture_output=True, text=True, cwd=warm, timeout=180, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return _Rig(hook, own)


def test_a_a_prepared_tree_holds_no_pyc_and_no_record_and_the_child_reads_the_prefix(rig: _Rig, tmp_path: Path) -> None:
    """受入: 準備を終えた木の下に __pycache__ と焼きの記録の file が 1 つも無く、記録と .pyc は置き場の木を写した dir に在り、子が
    root の venv の口の置き場の .pyc を読んで木の source を compile し直さない。直す前は木の中に __pycache__・
    .doeff-closure-table.json・.doeff-code-ready.json を書いた。"""
    tree, prefix = tmp_path / "root" / "repo", tmp_path / "root" / ".doeff-pycache"
    _write(tree, _files(tmp_path.name))
    _bake(tree, tmp_path / "store", rig, prefix)
    assert _tree_writes(tree) == []
    mirrored = _mirror(prefix, tree)
    assert (mirrored / ".doeff-code-ready.json").is_file()
    assert (mirrored / ".doeff-closure-table.json").is_file()
    tag = sys.implementation.cache_tag
    assert (mirrored / "use" / f"user.{tag}.pyc").is_file()
    read = _child(tree, prefix, rig)
    assert read.compiled == []
    assert read.cached == str(mirrored / "use" / f"user.{tag}.pyc")
    assert read.total == 102
    assert _tree_writes(tree) == []


def test_b_a_copied_tree_carries_the_source_roots_prefix_pyc_and_writes_nothing_into_the_tree(rig: _Rig, tmp_path: Path) -> None:
    """写し元の root も置き場を使う時(--inherit-pycache-prefix)、写し元と同じ file の source の .pyc は写し元の置き場から引き継ぎ
    (carried が計画の数)、新しい木にも何も書かない。子は新しい置き場から読み、compile し直さない。"""
    first_root, second_root = tmp_path / "first", tmp_path / "second"
    first_tree, second_tree = first_root / "repo", second_root / "repo"
    _write(first_tree, _files(tmp_path.name))
    _bake(first_tree, tmp_path / "store", rig, first_root / ".doeff-pycache")
    _copy_tree(first_tree, second_tree)
    stderr = _bake(second_tree, tmp_path / "store", rig, second_root / ".doeff-pycache", inherit=first_tree,
                   inherit_prefix=first_root / ".doeff-pycache")
    _assert_carried_unchecked(stderr)
    assert _tree_writes(first_tree) == []
    assert _tree_writes(second_tree) == []
    read = _child(second_tree, second_root / ".doeff-pycache", rig)
    assert read.compiled == []
    assert read.total == 102


def test_c_from_a_source_root_before_the_prefix_the_tool_reads_the_source_trees_own_pyc(rig: _Rig, tmp_path: Path) -> None:
    """置き場を使う前の形の写し元の root(--inherit-pycache-prefix の dir が無い)からは、写し元の木の中の __pycache__ と閉包の表を
    読んで引き継ぐ(版上げの最初の root が全部を照らし直さない)。新しい木には何も書かない。"""
    first_root, second_root = tmp_path / "first", tmp_path / "second"
    first_tree, second_tree = first_root / "repo", second_root / "repo"
    _write(first_tree, _files(tmp_path.name))
    _bake(first_tree, tmp_path / "store", rig, None)
    assert (first_tree / ".doeff-closure-table.json").is_file()
    _copy_tree(first_tree, second_tree)
    stderr = _bake(second_tree, tmp_path / "store", rig, second_root / ".doeff-pycache", inherit=first_tree,
                   inherit_prefix=first_root / ".doeff-pycache")
    _assert_carried_unchecked(stderr)
    assert _tree_writes(second_tree) == []
    read = _child(second_tree, second_root / ".doeff-pycache", rig)
    assert read.compiled == []
    assert read.total == 102


def test_d_the_venv_startup_hook_keeps_the_standard_library_beside_its_source(rig: _Rig, tmp_path: Path) -> None:
    """root の venv の起動の口: 置き場の file の在る venv の process で、標準 library の module の .pyc は隣の __pycache__(置き場で
    探すと起動のたびに compile し直す)、木の source の .pyc は置き場の木を写した dir(環境から継いだ別の root の置き場ではない)。"""
    prefix, source = tmp_path / ".doeff-pycache", tmp_path / "repo" / "m.py"
    completed = subprocess.run(
        [*_env(rig, tmp_path / "other", tmp_path / "where.log", site=_site(tmp_path, prefix)), sys.executable, "-c", _WHERE,
         str(source)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    stdlib, tree = completed.stdout.split()[-2:]
    import json

    assert stdlib == str(Path(json.__file__).parent / "__pycache__" / f"__init__.{sys.implementation.cache_tag}.pyc")
    assert tree == str(_mirror(prefix, source.parent) / f"m.{sys.implementation.cache_tag}.pyc")


def test_e_a_venv_without_a_place_ignores_the_inherited_prefix(rig: _Rig, tmp_path: Path) -> None:
    """置き場の file が空の venv(焼く木の無かった root)の口は、環境から継いだ別の root の置き場を使わず、木の source の .pyc を隣の
    __pycache__ に置く(別の root の置き場へ書かない — 2026-11-15 の本番で root の子が別の root の PYTHONPYCACHEPREFIX を持っていた)。"""
    source = tmp_path / "repo" / "m.py"
    completed = subprocess.run(
        [*_env(rig, tmp_path / "other", tmp_path / "where.log", site=_site(tmp_path, None)), sys.executable, "-c", _WHERE,
         str(source)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    tree = completed.stdout.split()[-1]
    assert tree == str(source.parent / "__pycache__" / f"m.{sys.implementation.cache_tag}.pyc")
