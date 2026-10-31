"""bytecode の準備の道具(worker/entry/code_prepare.hy)は、source の中身で引く保存先(doeff-hy の code_store)から .pyc を書き、無い物と
今の macro に合わない物だけを焼く(#3858)。

直す前は、前の版の木(--from)から変わっていない file(--changed の外)の .pyc を hardlink で引き継ぐだけだった。引き継ぎ元の root が無い
準備 — 新しい worker・空の /work・テストの 1 台の cluster・版をまたいだ別の dir の木 — は全部を焼き直し、日次の Pod の上の
test_same_program_daily.hy の準備は bytecode に 94.7 秒(1,130 file)かかった。直した後は、版(commit)と木の dir が違っても中身の同じ
source は保存先から書き、焼くのは中身の変わった file と、macro の出所(Hy の require の先)の中身が変わった使う側だけ。

台 = 4 つの package: macro の定義元(mac)・その macro を使う側(use)・macro の定義元を import だけする側(imp — require しないので
展開は macro に依らない)・無関係(other)。1 回目の木を焼いた後、2 回目は別の dir の木(別の版の展開に当たる)を、引き継ぎの引数なしで
同じ保存先を使って焼く(本番の準備と同じ引数の形 — 道具は --from を持たない)。

焼いた物の観測: 道具の process と焼きの子 process の起動の時に読まれる sitecustomize が、Hy の source の compile の合流点
(``importlib.machinery.SourceFileLoader.source_to_code`` — 起動時の .pth の doeff-hy の包みの外側。道具が明示に焼く口
source_to_code_as_import も、焼いている file の Hy の require が macro の定義元を import する時の compile もここを通る)を、受けた
Hy の path を log へ 1 行書いてから元を呼ぶ物に包む(道具の外から数える — 道具の答えを信じない)。数えるのは木の中の path だけ(道具
自身の import の compile は木の外)。明示に焼く口だけを数えると、名指しの保存先が .pyc を書かない設定でも埋まるようになった後
(doeff 9f6d6886c・#3737)、use の require の途中で compile された mac の code が保存先に入り、道具が mac を焼く番には保存先から
書くので、mac が 1 回も数えられなかった(#4172)。

速さ(1 件 30 秒の上限): 道具とその焼きの子 process は起動のたびに doeff の module を import する。.pyc を書かない設定のままだと毎回
source から compile して 1 回 15 秒かかるので、この file の最初の 1 回(module の fixture)だけ .pyc を書かせて、その .pyc を
PYTHONPYCACHEPREFIX の dir(checkout の外)に置き、以後の起動は読むだけにする。保存先は件ごとに別の dir で、台の file の中身は
件ごとに違う(註の行 — 件をまたいで保存先の entry を共有しない)。
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import hy  # noqa: F401 — 下の Hy の module(翻訳と答え手)を import できるようにする
import pytest
from doeff import do, run, with_handlers
from doeff_core_effects import reader, state
from doeff_core_effects import os_file_handler
from doeff_core_effects import subprocess_handler
from doeff_time import sync_time_handler

from doeff_cluster import MaterializeTree
from doeff_cluster._worker.intent.env_prepare_model import (
    TREE_COPY,
    TREE_EXPAND,
    TreePlaced,
    TreeSource,
)
from doeff_cluster import env_translation

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
    """道具を起こし、compile した木の中の Hy の source と報告の行の数を返す(木の外 = 道具自身の import の compile は数えない)。"""
    compiled = tree.parent / f"{tree.name}.compiled.log"
    completed = _tool(tree, store, rig.hook, rig.prefix, compiled, write=False)
    assert completed.returncode == 0, completed.stderr
    lines = compiled.read_text().split() if compiled.exists() else []
    names = sorted(str(Path(line).relative_to(tree)) for line in lines if Path(line).is_relative_to(tree))
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
    code の記録が今の macro の file と合わない)。import だけする側と無関係は保存先から書く。import した値は新しい macro の展開。
    焼き直した物は外からの compile の観測で断言する。道具の報告の rebuilt は道具が自分で焼いた数で、使う側を先に焼くと、その require が
    macro の定義元を compile して保存先へ足し(名指しの保存先は .pyc を書かない設定でも埋まる — doeff 9f6d6886c・#3737)、道具は定義元の
    .pyc を保存先から書く(stored に数える)— どちらを先に焼くかで 1 にも 2 にもなる(#4172)。"""
    second = tmp_path / "second"
    _write_four(second, filled.salt, macro_add=5)
    baked = _bake(second, filled.store, filled.rig)
    assert baked.compiled == ["mac/macros.hy", "use/user.hy"], baked.compiled
    assert 1 <= baked.counts.get("rebuilt", 0) <= 2 and baked.counts.get("failed") == 0, baked.counts
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


def _pycs(tree: Path) -> dict[str, Path]:
    """木の __pycache__ の .pyc(相対 path → path)。"""
    return {str(p.relative_to(tree)): p for p in sorted(tree.rglob("__pycache__/*.pyc"))}


def test_f_trees_baked_from_the_store_share_one_file_per_pyc(filled: _Store, tmp_path: Path) -> None:
    """失敗ケース(f): 保存先の code から置く .pyc は、root をまたいで保存先の 1 つの file を分け合い、2 つ目の木は
    .pyc の新しい block を持たない(hardlink — 私有の byte 0)。直す前は木ごとに同じ中身を新しく書き、Mac の root 1 本で約 100 MB を書いて、
    root を消した後も local snapshot が 24 時間持った。"""
    second, third = tmp_path / "second", tmp_path / "third"
    for tree in (second, third):
        _write_four(tree, filled.salt)
        baked = _bake(tree, filled.store, filled.rig)
        assert baked.compiled == [] and baked.counts.get("stored", 0) >= len(_HY_SOURCES), baked.counts
        assert "hardlink で置けない" not in baked.stderr, baked.stderr
    placed, again = _pycs(second), _pycs(third)
    assert set(placed) == set(again) and len(placed) >= len(_HY_SOURCES), (sorted(placed), sorted(again))
    for rel, path in again.items():
        assert path.stat().st_ino == placed[rel].stat().st_ino, ("同じ中身の .pyc が別の実体", rel)
        assert path.stat().st_nlink >= 3, ("保存先と 2 つの木の 3 つの名", rel, path.stat().st_nlink)
    assert sum(p.stat().st_blocks for p in again.values() if p.stat().st_nlink == 1) == 0, "2 つ目の木が .pyc の新しい block を持つ"
    assert _import_value(third, filled) == 2


def test_g_rebaking_one_tree_does_not_write_through_the_shared_pyc(filled: _Store, tmp_path: Path) -> None:
    """失敗ケース(g・hardlink の先を書き換えない): 2 つの木が .pyc を分け合った後、片方の木で source を変えて焼き直しても、もう片方の木の
    .pyc の中身と import した値は前のまま(置き換えで置く — その場で開いて書かない)。"""
    second, third = tmp_path / "second", tmp_path / "third"
    for tree in (second, third):
        _write_four(tree, filled.salt)
        _bake(tree, filled.store, filled.rig)
    before = {rel: p.read_bytes() for rel, p in _pycs(second).items()}
    _write_four(third, filled.salt, macro_add=7)
    baked = _bake(third, filled.store, filled.rig)
    assert baked.compiled == ["mac/macros.hy", "use/user.hy"], baked.compiled
    assert {rel: p.read_bytes() for rel, p in _pycs(second).items()} == before, "焼き直しが他の木の .pyc を書き換えた"
    assert _import_value(second, filled) == 2
    assert _import_value(third, filled) == 8


#: 2 つの版の木の中身の量を root の大きさに近づける業務の module の数(1 つの版の差は、そのうちの 1 つ)。
_BULK_MODULES = 40


def _materialize(mirror: Path, commit: str, dest: Path, source: TreeSource | None, tmp: Path) -> TreePlaced:
    """worker の準備の process と同じ並び(本物の答え手 + 翻訳 env-translation)で、MaterializeTree を 1 回出して木を置く。"""
    settings = {"runtime-env.state": str(tmp / "state"), "runtime-env.shared": str(tmp / "state"),
                "runtime-env.uv-cache": str(tmp / "uv-cache"), "runtime-env.repo-keys": {}, "runtime-env.code-prepare": "",
                "runtime-env.uv": "uv", "runtime-env.progress": "", "runtime-env.notes": "/dev/null"}

    @do
    def placed() -> object:
        answer = yield MaterializeTree(str(mirror), commit, str(dest), source)
        return answer

    return run(with_handlers([state(), sync_time_handler(), reader(settings), subprocess_handler, os_file_handler, env_translation],
                             placed()))


def _git(repo: Path, *args: str) -> str:
    """検の手元の repo で git を 1 回撃ち、標準出力を返す。"""
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def _inodes(*dirs: Path) -> dict[tuple[int, int], int]:
    """dirs の下の普通の file の実体(#(device inode))→ 割り当てた byte。"""
    found: dict[tuple[int, int], int] = {}
    for base in dirs:
        for path in base.rglob("*"):
            st = path.lstat()
            if stat.S_ISREG(st.st_mode):
                found[(st.st_dev, st.st_ino)] = st.st_blocks * 512
    return found


def test_h_a_one_commit_root_writes_a_tenth_or_less_where_cp_refuses_gnu_options(
        filled: _Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """受入: 1 commit の差の root(木の写し + bytecode の焼き)が新しく書く byte(root と保存先に新しくできた実体の
    block)は、今の形(木を全部展開し、.pyc を全部書く)の 10 分の 1 以下。PATH の頭には macOS の BSD の cp と同じく GNU の書き方を断る cp を
    置く — 直す前の Mac は、木の写しが cp に断られて全部を展開し(10-17 19:53 JST の root は 2 つの repo の木とも展開)、.pyc を
    root ごとに新しく書いた(root 1 本 私有 429 MB)。"""
    bsd = tmp_path / "bsd-bin"
    bsd.mkdir()
    (bsd / "cp").write_text("#!/bin/sh\necho 'cp: illegal option -- -' >&2\nexit 64\n")
    (bsd / "cp").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bsd}:{os.environ.get('PATH', '')}")  # noqa: DOEFF004 — 検が PATH の先頭へ道具の dir を足す 1 か所
    repo = tmp_path / "app-repo"
    _write_four(repo, filled.salt)
    for i in range(_BULK_MODULES):
        path = repo / "bulk" / f"m{i}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {filled.salt}\n" + "".join(f"def f{j}(x):\n    return x + {i * 100 + j}\n" for j in range(60)))
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "v1")
    v1 = _git(repo, "rev-parse", "HEAD")
    (repo / "bulk" / "m0.py").write_text((repo / "bulk" / "m0.py").read_text() + "VALUE = 2\n")
    _git(repo, "commit", "-q", "-am", "v2")
    v2 = _git(repo, "rev-parse", "HEAD")
    mirror = tmp_path / "app.git"
    subprocess.run(["git", "clone", "--bare", "-q", str(repo), str(mirror)], check=True, capture_output=True)
    first, second = tmp_path / "root-1" / "app", tmp_path / "root-2" / "app"
    assert _materialize(mirror, v1, first, None, tmp_path).how == TREE_EXPAND
    _bake(first, filled.store, filled.rig)
    before = _inodes(first.parent, filled.store)
    placed = _materialize(mirror, v2, second, TreeSource(dir=str(first), commit=v1), tmp_path)
    assert (placed.how, placed.reason) == (TREE_COPY, ""), placed
    baked = _bake(second, filled.store, filled.rig)
    assert baked.counts.get("failed") == 0 and baked.counts.get("rebuilt", 0) >= 1, baked.counts
    whole = sum(_inodes(second.parent).values())
    written = sum(size for inode, size in _inodes(second.parent, filled.store).items() if inode not in before)
    assert written * 10 <= whole, ("1 commit の差の root が新しく書いた byte", written, "今の形", whole)
    assert _import_value(second, filled) == 2


def test_e_two_macro_versions_taking_turns_do_not_rebake_each_other(filled: _Store, tmp_path: Path) -> None:
    """失敗ケース(e): macro の中身の違う 2 つの書き手(実行環境の準備と、doeff の版の違う作業木の import の代役 — 使う側の source は同じ)が、
    同じ保存先で新しい木を交互に焼く。鍵 1 つに entry 1 つの並びでは、互いの entry を今の macro に合わないと見て使う側を焼き直し、同じ鍵を
    上書きし合った(2026-10-12 zeus — 同じ版の準備でも 1 回に 100〜600 本)。鍵 1 つに macro の環境ごとの entry を
    持つ並びでは、2 巡目からはどちらも焼き直さず、import した値はそれぞれの macro の展開。"""
    five = tmp_path / "five-1"
    _write_four(five, filled.salt, macro_add=5)
    assert _bake(five, filled.store, filled.rig).compiled == ["mac/macros.hy", "use/user.hy"]
    for turn in (2, 3):
        for name, add in (("one", 1), ("five", 5)):
            tree = tmp_path / f"{name}-{turn}"
            _write_four(tree, filled.salt, macro_add=add)
            baked = _bake(tree, filled.store, filled.rig)
            assert baked.compiled == [], (name, turn, baked.compiled)
            assert _import_value(tree, filled) == 1 + add
