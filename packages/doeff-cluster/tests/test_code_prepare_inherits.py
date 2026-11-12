"""bytecode の準備の道具(worker/entry/code_prepare.hy)は、写し元の木(--inherit — 準備がこの木を hardlink で写した、依存のキーの同じ
完成済みの root の木)が在れば、写し元と同じ file の source の .pyc と閉包の表を引き継ぎ、仕事を写し元からの差の大きさにする
(ADR-DOE-BUILD-002 R3c)。

直す前は、新しい commit の root の木の .pyc は 0 本から始まり、閉包の約 3,250 本の全部を、焼きの道具が 1 本ずつ保存先の entry を読んで
今の macro と照らしてから置き、閉包も約 3,250 本の source と .imports の entry を毎回読み直した。焼き直しはほぼ 0 本なのに、CPU の飽和した
機体で照らしが 36〜109 秒・閉包が 7〜72 秒かかった(2026-11-10)。直した後は、写し元と同じ file の source は照らさずに残し(依る macro の
module が変わらない物だけ)、閉包の import の名も写し元の表から読む。

台 = 9 つの Hy の source: macro の定義元(mac)・その macro を使う側(use)・macro の定義元を import だけする側(imp)・無関係(other)・
葉 5 つ(l1〜l5)と、全部を import する入口(app.main — --entries)。1 回目の木を焼いた後、2 回目の木は 1 回目の木の source を hardlink で
写し(準備の MaterializeTree と同じ — 変わる file だけを新しく書く)、--inherit で 1 回目の木を渡して焼く。

焼いた物の観測は test_code_prepare_content_store.py と同じ sitecustomize の compile の数え(道具の外から数える)と、道具の報告の行(木の
stored・rebuilt・reused と、焼きの道具の「写し元の木から引き継いだ .pyc: carried=… trusted=… checked=…」の行)。速さのため、道具自身の
import の .pyc は module の最初の 1 回だけ書かせて PYTHONPYCACHEPREFIX の dir に置く(content_store の test と同じ)。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

#: 準備の道具(本番の準備の処理ステージが起こす script と同じ file)。
TOOL = Path(__file__).resolve().parents[1] / "src" / "doeff_cluster" / "_worker" / "entry" / "code_prepare.hy"

_COMPILED_HOOK = """\
import os

_LOG = os.environ.get("BAKE_COMPILED_LOG")
if _LOG:
    import importlib.machinery as _machinery

    _original = _machinery.SourceFileLoader.source_to_code

    def source_to_code(self, data, path, *args, **kwargs):
        if isinstance(path, str) and path.endswith(".hy"):
            with open(_LOG, "a") as log:
                log.write(path + "\\n")
        return _original(self, data, path, *args, **kwargs)

    _machinery.SourceFileLoader.source_to_code = source_to_code
"""

_LEAVES = ("l1", "l2", "l3", "l4", "l5")
_HY_SOURCES = ("mac/macros.hy", "use/user.hy", "imp/importer.hy", "other/plain.hy", *(f"{leaf}/leaf.hy" for leaf in _LEAVES))

#: 読む側 — 子の job と同じく、用意した木を import して値を読む。
_IMPORT = """\
import sys
sys.path.insert(0, sys.argv[1])
import hy
sys.pycache_prefix = None
import app.main
print(app.main.total)
"""


def _files(salt: str, *, macro_add: int = 1, leaf: int = 0) -> dict[str, str]:
    """台の file の中身(salt = 件ごとに中身を変える註の行・macro_add = macro の展開の値・leaf = 葉の値を変える数)。"""
    files = {
        "app/__init__.py": f"# {salt}\n",
        "app/main.py": (
            f"# {salt}\nimport use.user\nimport imp.importer\nimport other.plain\n"
            + "".join(f"import {name}.leaf\n" for name in _LEAVES)
            + "total = use.user.value + imp.importer.value + other.plain.value + "
            + " + ".join(f"{name}.leaf.value" for name in _LEAVES)
            + "\n"
        ),
        "mac/__init__.py": f"# {salt}\n",
        "mac/macros.hy": f"; {salt}\n(defmacro answer [] (+ 1 {macro_add}))\n",
        "use/__init__.py": f"# {salt}\n",
        "use/user.hy": f"; {salt}\n(require mac.macros [answer])\n(setv value (answer))\n",
        "imp/__init__.py": f"# {salt}\n",
        "imp/importer.hy": f"; {salt}\n(import mac.macros)\n(setv value 10)\n",
        "other/__init__.py": f"# {salt}\n",
        "other/plain.hy": f"; {salt}\n(setv value 100)\n",
    }
    for name in _LEAVES:
        files[f"{name}/__init__.py"] = f"# {salt}\n"
        files[f"{name}/leaf.hy"] = f"; {salt}\n(setv value {1000 + leaf})\n"
    return files


def _write(root: Path, files: dict[str, str]) -> None:
    """file を置く。"""
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _copy_tree(source: Path, dest: Path, changed: dict[str, str]) -> None:
    """source の木の source を dest へ hardlink で写し(__pycache__ と根の隠し file — 完成の印・閉包の表 — は写さない)、changed の file
    だけを新しく書く(準備の MaterializeTree と同じ形 — 変わった path の旧い file は写さない)。"""
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source)
        if path.is_dir() or "__pycache__" in rel.parts or rel.name.startswith(".") or rel.as_posix() in changed:
            continue
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        os.link(path, dest / rel)
    _write(dest, changed)


@dataclass(frozen=True)
class _Rig:
    """件をまたいで使う物: hook = compile を数える sitecustomize の dir・prefix = 道具自身の import の .pyc の dir(checkout の外)。"""

    hook: Path
    prefix: Path


@dataclass(frozen=True)
class _Bake:
    """1 回の焼きの観測: compiled = compile の口を通った木の中の Hy の source(名の順)・counts = 木の報告の行の数・carried = 焼きの道具の
    引き継ぎの行の数(carried・trusted・checked — 行が無ければ空)。"""

    compiled: list[str]
    counts: dict[str, int]
    carried: dict[str, int]
    stderr: str


def _tool(tree: Path, store: Path, rig: _Rig, compiled: Path, *, inherit: Path | None, write: bool = False) -> subprocess.CompletedProcess[str]:
    """本番の準備と同じ引数の形で道具を起こす(--entries app.main・在れば --inherit)。"""
    return subprocess.run(
        [
            "env", *(["-u", "PYTHONDONTWRITEBYTECODE"] if write else ["PYTHONDONTWRITEBYTECODE=1"]), f"PYTHONPYCACHEPREFIX={rig.prefix}",
            f"DOEFF_HY_CODE_STORE={store}", f"PYTHONPATH={rig.hook}", f"BAKE_COMPILED_LOG={compiled}",
            sys.executable, "-m", "hy", str(TOOL), "--revision", "r", "--tree", str(tree), "--roots", ".", "--jobs", "1",
            "--entries", "app.main", *(["--inherit", str(inherit)] if inherit is not None else []),
        ],
        capture_output=True, text=True, cwd=tree, timeout=180, check=False,
    )


def _bake(tree: Path, store: Path, rig: _Rig, *, inherit: Path | None) -> _Bake:
    """道具を起こし、compile した木の中の Hy の source と報告の行の数を返す。"""
    compiled = tree.parent / f"{tree.name}.compiled.log"
    completed = _tool(tree, store, rig, compiled, inherit=inherit)
    assert completed.returncode == 0, completed.stderr
    lines = compiled.read_text().split() if compiled.exists() else []
    names = sorted({str(Path(line).relative_to(tree)) for line in lines if Path(line).is_relative_to(tree)})
    tree_line = next((line for line in completed.stderr.splitlines() if f"tree={tree} " in line), "")
    counts = {k: int(v) for k, v in (part.split("=", 1) for part in tree_line.split() if "=" in part) if v.isdigit()}
    carried_line = re.search(r"carried=(\d+) trusted=(\d+) checked=(\d+)", completed.stderr)
    carried = {} if carried_line is None else dict(zip(("carried", "trusted", "checked"), map(int, carried_line.groups())))
    return _Bake(names, counts, carried, completed.stderr)


def _import_total(tree: Path, rig: _Rig, store: Path) -> int:
    """用意した木を子の job と同じく import し、app.main の合計を読む(子は bytecode を書かない)。"""
    completed = subprocess.run(
        ["env", f"PYTHONPYCACHEPREFIX={rig.prefix}", "PYTHONDONTWRITEBYTECODE=1", f"DOEFF_HY_CODE_STORE={store}",
         sys.executable, "-c", _IMPORT, str(tree)],
        capture_output=True, text=True, cwd=tree, timeout=180, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return int(completed.stdout.split()[-1])


def _pyc_files(tree: Path) -> dict[str, tuple[int, bytes]]:
    """木の .pyc ごとの #(inode 中身)(写し元の .pyc が書き換えられていない事を見る)。"""
    return {str(p.relative_to(tree)): (p.stat().st_ino, p.read_bytes()) for p in sorted(tree.rglob("*.pyc"))}


@pytest.fixture(scope="module")
def rig(tmp_path_factory: pytest.TempPathFactory) -> _Rig:
    """hook を置き、道具を 1 度だけ .pyc を書く設定で起こして、道具自身の import の .pyc を prefix の dir に作る。"""
    base = tmp_path_factory.mktemp("rig")
    hook, prefix, warm = base / "hook", base / "pyc", base / "warm"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text(_COMPILED_HOOK)
    _write(warm, _files("warm"))
    completed = _tool(warm, base / "store", _Rig(hook, prefix), base / "warm.compiled.log", inherit=None, write=True)
    assert completed.returncode == 0, completed.stderr
    return _Rig(hook, prefix)


@dataclass(frozen=True)
class _First:
    """1 回目に焼いた木(写し元)・その保存先・件の註。"""

    tree: Path
    store: Path
    salt: str


@pytest.fixture
def first(tmp_path: Path, rig: _Rig) -> _First:
    """1 回目の木を空の保存先で焼く(全部を焼き、閉包の表を木の根に置く)。"""
    store, salt, tree = tmp_path / "store", tmp_path.name, tmp_path / "first"
    _write(tree, _files(salt))
    baked = _bake(tree, store, rig, inherit=None)
    assert baked.compiled == sorted(_HY_SOURCES), baked.compiled
    assert (tree / ".doeff-closure-table.json").is_file()
    return _First(tree, store, salt)


def test_a_five_changed_sources_are_the_only_work_and_the_rest_is_reused_unchecked(first: _First, rig: _Rig, tmp_path: Path) -> None:
    """受入(1): 写し元から source の 5 file だけが変わった版では、焼きの道具が保存先を引くか焼く数が変わった 5 本以下になり、残りは
    照らさずに reused になる(trusted = reused・checked = 0)。compile も変わった 5 本だけ。写し元の木の .pyc は書き換わらない。
    直す前は、写し元の .pyc を引き継がず、全部(計画の全数)を保存先で引いて照らした(reused 0)。"""
    before = _pyc_files(first.tree)
    changed = {f"{name}/leaf.hy": f"; {first.salt}\n(setv value {2000 + index})\n" for index, name in enumerate(_LEAVES)}
    second = tmp_path / "second"
    _copy_tree(first.tree, second, changed)
    baked = _bake(second, first.store, rig, inherit=first.tree)
    assert baked.compiled == sorted(changed), baked.compiled
    looked_up = baked.counts.get("stored", 0) + baked.counts.get("rebuilt", 0) + baked.counts.get("fetched", 0)
    assert looked_up <= len(changed), baked.counts
    assert baked.counts.get("reused", 0) >= len(_HY_SOURCES) - len(changed), baked.counts
    assert baked.carried.get("trusted") == baked.counts.get("reused") and baked.carried.get("checked") == 0, (baked.carried, baked.counts)
    assert "closure_carried=" in baked.stderr and "closure_reread=5 " in baked.stderr, baked.stderr
    assert _import_total(second, rig, first.store) == 2 + 10 + 100 + sum(2000 + i for i in range(len(_LEAVES)))
    assert _pyc_files(first.tree) == before


def test_b_a_changed_macro_definition_checks_its_users_again_instead_of_reusing(first: _First, rig: _Rig, tmp_path: Path) -> None:
    """受入(2): macro の定義元が変わった版では、その macro を使う側(use)は照らさずに reused にはならず、照らし直されて新しい展開で
    焼き直される(checked ≥ 1・compile に use が在る)。macro の定義元を import だけする側(imp)・無関係・葉は照らさずに残す。"""
    second = tmp_path / "second"
    _copy_tree(first.tree, second, {"mac/macros.hy": f"; {first.salt}\n(defmacro answer [] (+ 1 5))\n"})
    baked = _bake(second, first.store, rig, inherit=first.tree)
    assert baked.compiled == ["mac/macros.hy", "use/user.hy"], baked.compiled
    assert baked.carried.get("checked", 0) >= 1, baked.carried
    assert baked.carried.get("trusted", 0) >= len(_HY_SOURCES) - 2, baked.carried
    assert baked.counts.get("reused", 0) == baked.carried.get("trusted"), (baked.counts, baked.carried)
    assert _import_total(second, rig, first.store) == 6 + 10 + 100 + 5 * 1000


def test_c_without_a_source_tree_the_preparation_writes_from_the_store_as_before(first: _First, rig: _Rig, tmp_path: Path) -> None:
    """写し元を渡さない準備(写し元の無い root・依存のキーの違う root)は今までどおり保存先から書く: 引き継ぎの行は無く、焼き直しは 0。"""
    second = tmp_path / "second"
    _write(second, _files(first.salt))
    baked = _bake(second, first.store, rig, inherit=None)
    assert baked.carried == {} and baked.compiled == [], (baked.carried, baked.compiled)
    assert baked.counts.get("rebuilt") == 0 and baked.counts.get("stored", 0) >= len(_HY_SOURCES), baked.counts
