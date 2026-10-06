"""bytecode の準備の道具(worker/entry/code_prepare.hy)は、source の中身で引く保存先(doeff-hy の code_store)から .pyc を書き、無い物と
今の macro に合わない物だけを焼く(#3858)。

直す前は、前の版の木(--from)から変わっていない file(--changed の外)の .pyc を hardlink で引き継ぐだけだった。引き継ぎ元の root が無い
準備 — 新しい worker・空の /work・テストの 1 台の cluster・版をまたいだ別の dir の木 — は全部を焼き直し、日次の Pod の上の
test_same_program_daily.hy の準備は bytecode に 94.7 秒(1,130 file)かかった。直した後は、版(commit)と木の dir が違っても中身の同じ
source は保存先から書き、焼くのは中身の変わった file と、macro の出所(Hy の require の先)の中身が変わった使う側だけ。

台 = 4 つの package: macro の定義元(mac)・その macro を使う側(use)・macro の定義元を import だけする側(imp — require しないので
展開は macro に依らない)・無関係(other)。1 回目の木を焼いた後、2 回目は別の dir の木(別の版の展開に当たる)を、引き継ぎの引数なしで
同じ保存先を使って焼く(本番の準備と同じ引数の形 — 道具は --from を持たない)。

焼いた物の観測: 道具の process と焼きの子 process の起動の時に読まれる sitecustomize が、root の側の compile の口
(python_bytecode の source_to_code_as_import — 直す前の compile-one も直した後の compiled-pyc もここを通る)を、受けた path を log へ
1 行書いてから元を呼ぶ物に包む(道具の外から数える — 道具の答えを信じない)。

速さ(1 件 30 秒の上限): 道具とその焼きの子 process は起動のたびに doeff の module を import する。.pyc を書かない設定のままだと毎回
source から compile して 1 回 15 秒かかるので、この file の最初の 1 回(module の fixture)だけ .pyc を書かせて、その .pyc を
PYTHONPYCACHEPREFIX の dir(checkout の外)に置き、以後の起動は読むだけにする。保存先は件ごとに別の dir で、台の file の中身は
件ごとに違う(註の行 — 件をまたいで保存先の entry を共有しない)。
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

#: 準備の道具(本番の準備の処理ステージが起こす script と同じ file)。
TOOL = Path(__file__).resolve().parents[1] / "src" / "doeff_cluster" / "worker" / "entry" / "code_prepare.hy"

_COMPILED_HOOK = """\
import os

_LOG = os.environ.get("BAKE_COMPILED_LOG")
if _LOG:
    import doeff_core_effects.python_bytecode as _bytecode

    _original = _bytecode.source_to_code_as_import

    def source_to_code_as_import(loader, data, path):
        with open(_LOG, "a") as log:
            log.write(path + "\\n")
        return _original(loader, data, path)

    _bytecode.source_to_code_as_import = source_to_code_as_import
"""

_HY_SOURCES = ("mac/macros.hy", "use/user.hy", "imp/importer.hy", "other/plain.hy")

#: 読む側 — 子の job と同じく、用意した木を import して値を読む(保存先の code が今の macro の展開であることを確かめる)。
_IMPORT = """\
import sys
sys.path.insert(0, sys.argv[1])
import hy
sys.pycache_prefix = None
import use.user
print(use.user.value)
"""


def _write_four(root: Path, salt: str, *, macro_add: int = 1, importer: int = 2, plain: int = 3) -> None:
    """macro の定義元・使う側・import だけする側・無関係の 4 package を置く(数は file の中身を変えるため・salt = 件ごとに中身を変える
    註の行)。"""
    files = {
        "mac/__init__.py": f"# {salt}\n",
        "mac/macros.hy": f"; {salt}\n(defmacro answer [] (+ 1 {macro_add}))\n(setv TAG \"mac\")\n",
        "use/__init__.py": f"# {salt}\n",
        "use/user.hy": f"; {salt}\n(require mac.macros [answer])\n(setv value (answer))\n",
        "imp/__init__.py": f"# {salt}\n",
        "imp/importer.hy": f"; {salt}\n(import mac.macros)\n(setv value {importer})\n",
        "other/__init__.py": f"# {salt}\n",
        "other/plain.hy": f"; {salt}\n(setv value {plain})\n",
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


@dataclass(frozen=True)
class _Bake:
    """1 回の焼きの観測: compiled = compile の口を通った Hy の source の相対 path(名の順)・counts = 木の報告の行の数・stderr = 道具の
    stderr。"""

    compiled: list[str]
    counts: dict[str, int]
    stderr: str


def _tool(tree: Path, store: Path, hook: Path, prefix: Path, compiled: Path, *, write: bool) -> subprocess.CompletedProcess[str]:
    """本番の準備と同じ引数の形で道具を起こす(引き継ぎの引数は無い)。write = 道具自身の import の .pyc を prefix の dir に書かせる
    (module の最初の 1 回だけ)。"""
    return subprocess.run(
        [
            "env", *(["-u", "PYTHONDONTWRITEBYTECODE"] if write else ["PYTHONDONTWRITEBYTECODE=1"]), f"PYTHONPYCACHEPREFIX={prefix}",
            f"DOEFF_HY_CODE_STORE={store}", f"PYTHONPATH={hook}", f"BAKE_COMPILED_LOG={compiled}",
            sys.executable, "-m", "hy", str(TOOL), "--revision", "r", "--tree", str(tree), "--roots", ".", "--jobs", "1",
        ],
        capture_output=True, text=True, cwd=tree, timeout=180, check=False,
    )


def _bake(tree: Path, store: Path, rig: "_Rig") -> _Bake:
    """道具を起こし、compile した Hy の source と報告の行の数を返す。"""
    compiled = tree.parent / f"{tree.name}.compiled.log"
    completed = _tool(tree, store, rig.hook, rig.prefix, compiled, write=False)
    assert completed.returncode == 0, completed.stderr
    lines = compiled.read_text().split() if compiled.exists() else []
    names = sorted(str(Path(line).relative_to(tree)) for line in lines if line.endswith(".hy"))
    return _Bake(names, _counts(completed.stderr, tree), completed.stderr)


def _counts(report: str, tree: Path) -> dict[str, int]:
    """木ごとの報告の行(tree=<木> stored=… rebuilt=… reused=… failed=… problem=…)の数(行が無ければ空 — 呼び手の断言が赤になる)。"""
    for line in report.splitlines():
        if f"tree={tree} " in line:
            return {k: int(v) for k, v in (part.split("=", 1) for part in line.split() if "=" in part) if v.isdigit()}
    return {}


def _import_value(tree: Path, filled: "_Store") -> int:
    """用意した木を子の job と同じく import し、use.user の値を読む(子は bytecode を書かない)。Hy 自身は rig の .pyc から読み、木の
    module は木の __pycache__ の .pyc(道具が書いた物)から読む(script が Hy を読んだ後に pycache_prefix を外す)。"""
    completed = subprocess.run(
        ["env", f"PYTHONPYCACHEPREFIX={filled.rig.prefix}", "PYTHONDONTWRITEBYTECODE=1", f"DOEFF_HY_CODE_STORE={filled.store}",
         sys.executable, "-c", _IMPORT, str(tree)],
        capture_output=True, text=True, cwd=tree, timeout=180, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return int(completed.stdout.split()[-1])


@dataclass(frozen=True)
class _Rig:
    """件をまたいで使う物: hook = compile を数える sitecustomize の dir・prefix = 道具自身の import の .pyc の dir(checkout の外)。"""

    hook: Path
    prefix: Path


@pytest.fixture(scope="module")
def rig(tmp_path_factory: pytest.TempPathFactory) -> _Rig:
    """hook を置き、道具を 1 度だけ .pyc を書く設定で起こして、道具自身の import の .pyc を prefix の dir に作る。"""
    base = tmp_path_factory.mktemp("rig")
    hook, prefix, warm = base / "hook", base / "pyc", base / "warm"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text(_COMPILED_HOOK)
    _write_four(warm, "warm")
    completed = _tool(warm, base / "store", hook, prefix, base / "warm.compiled.log", write=True)
    assert completed.returncode == 0, completed.stderr
    return _Rig(hook, prefix)


@dataclass(frozen=True)
class _Store:
    """1 回目の焼きで満たした保存先・件の台の中身を変える註(salt)・1 回目の焼きが足した entry。"""

    store: Path
    rig: _Rig
    salt: str
    added: tuple[Path, ...]


@pytest.fixture
def filled(tmp_path: Path, rig: _Rig) -> _Store:
    """1 回目の木を空の保存先で焼く(Hy の source を全部焼き、保存先へ足す)。"""
    store, salt = tmp_path / "store", tmp_path.name
    first = tmp_path / "first"
    _write_four(first, salt)
    baked = _bake(first, store, rig)
    assert baked.compiled == sorted(_HY_SOURCES), baked.compiled
    return _Store(store, rig, salt, tuple(sorted(store.rglob("*.code"))))


def test_b_an_unchanged_file_is_not_recompiled_in_another_version_tree(filled: _Store, tmp_path: Path) -> None:
    """失敗ケース(b): 中身の変わらない file は、版(別の dir に展開した木)が替わっても焼き直さず、保存先の code から書く。直す前は
    引き継ぎ元を渡さない準備が全部(4 つ)を焼き直した。"""
    second = tmp_path / "second"
    _write_four(second, filled.salt)
    baked = _bake(second, filled.store, filled.rig)
    assert baked.compiled == [], baked.compiled
    assert baked.counts.get("stored", 0) >= len(_HY_SOURCES) and baked.counts.get("rebuilt") == 0, baked.counts
    assert _import_value(second, filled) == 2


def test_a_a_changed_file_is_recompiled_and_the_others_come_from_the_store(filled: _Store, tmp_path: Path) -> None:
    """失敗ケース(a): 中身の変わった file(macro の定義元以外)はそれだけを焼き直し、ほかは保存先から書く。"""
    second = tmp_path / "second"
    _write_four(second, filled.salt, importer=7)
    baked = _bake(second, filled.store, filled.rig)
    assert baked.compiled == ["imp/importer.hy"], baked.compiled
    assert baked.counts.get("rebuilt") == 1 and baked.counts.get("failed") == 0, baked.counts


def test_c_a_changed_macro_definition_recompiles_its_users_too(filled: _Store, tmp_path: Path) -> None:
    """失敗ケース(c): macro の出所(require の先)の中身が変わったら、その macro を使う側も焼き直す(使う側の source は同じでも、保存先の
    code の記録が今の macro の file と合わない)。import だけする側と無関係は保存先から書く。import した値は新しい macro の展開。"""
    second = tmp_path / "second"
    _write_four(second, filled.salt, macro_add=5)
    baked = _bake(second, filled.store, filled.rig)
    assert baked.compiled == ["mac/macros.hy", "use/user.hy"], baked.compiled
    assert baked.counts.get("rebuilt") == 2, baked.counts
    assert _import_value(second, filled) == 6


def test_d_a_broken_store_entry_is_named_and_rebuilt(filled: _Store, tmp_path: Path) -> None:
    """失敗ケース(d): 壊れた保存先の entry(code として読めない)は黙って使わず、名指しの 1 行を出して除き、焼き直して足し直す。"""
    assert filled.added, "1 回目の焼きが保存先へ code を足していない"
    for entry in filled.added:
        entry.write_bytes(b"\x00broken")
    second = tmp_path / "second"
    _write_four(second, filled.salt)
    baked = _bake(second, filled.store, filled.rig)
    assert baked.compiled == sorted(_HY_SOURCES), baked.compiled
    assert "bytecode の保存先の entry が壊れている" in baked.stderr, baked.stderr
    third = tmp_path / "third"
    _write_four(third, filled.salt)
    again = _bake(third, filled.store, filled.rig)
    assert again.compiled == [], ("足し直した entry を使わない", again.compiled)
