"""macro が変わった Hy の module の古い bytecode を使わない包み(doeff_hy_bytecode_guard・agora-redesign #1292)の反例。

#1003 の形: macro の提供元を変えても、使う側の module の .pyc は source の更新時刻と大きさしか見ない Python の判定で
有効とされ、古い展開のまま動く。包みがあれば、使う側は新しい展開で動く。

Hy は **PyPI の版**(fork ではない — 利用者の決定 2026-09-29「Hy には一切手を入れない」)を、この venv と切り離した
使い捨ての環境(``uv run --no-project --isolated --with hy==1.3.1``)で使う。この venv の Hy は fork で、fork 自身の
仕組み(.hydeps)が同じ赤を消してしまうので、ここでは包みの効き目を分けて測れない。
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import marshal
import os
import shutil
import subprocess
import sys
from collections.abc import Buffer
from pathlib import Path

import pytest
from doeff_hy_bytecode_guard import records

PYPI_HY = "hy==1.3.1"
#: 包みの package の親(doeff-hy の src)— 使い捨ての環境へは path で渡す(doeff-hy 全体は入れない)。
GUARD_ROOT = Path(__file__).resolve().parents[1] / "src"

#: 使い捨ての環境へ渡さない環境変数(この suite の固定 PYTHONDONTWRITEBYTECODE=1 は .pyc を書かせないので、
#: 子が .pyc を書けず #1003 の形にならない。venv の指定は uv が使い捨ての環境を作るのを妨げる)。
_ENV_NOT_PASSED = (
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONPYCACHEPREFIX",
    "PYTHONPATH",
)

_LOADER = """\
import sys
sys.path.insert(0, {root!r})
guard = {guard!r}


def load_guard():
    # 包みの package は checkout の中にあるので、その .pyc を checkout に書かない(root の conftest の検査)—
    # 記録の module も先に読んでおく(包みは Hy の source に初めて当たった時に読む)。
    sys.dont_write_bytecode = True
    sys.path.insert(0, {guard_root!r})
    import doeff_hy_bytecode_guard
    import doeff_hy_bytecode_guard.records
    import doeff_hy_bytecode_guard.macro_use
    doeff_hy_bytecode_guard.install()
    sys.dont_write_bytecode = False


if guard == "before-hy":
    load_guard()
import hy
if guard == "after-hy":
    load_guard()
import pkg.user
print(hy.__version__, pkg.user.value)
"""


def _write_package(root: Path) -> None:
    """macro の提供元(macros)と、macro が展開の時に呼ぶ補助(helpers)と、使う側(user)の 3 つ。"""
    package = root / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "helpers.hy").write_text("(defn base [] 10)\n")
    (package / "macros.hy").write_text(
        "(import pkg.helpers [base])\n(defmacro answer [] (+ (base) 1))\n"
    )
    (package / "user.hy").write_text("(require pkg.macros [answer])\n(setv value (answer))\n")


def _edit(path: Path, text: str) -> None:
    """file を書き換え、更新時刻を 2 秒進める — Python 自身の .pyc の判定は更新時刻を秒で見るので、同じ秒に同じ大きさで
    書き換えると提供元自身の .pyc が古いまま有効とされる(それは Python の限界で、この包みの対象ではない)。"""
    before = path.stat().st_mtime_ns
    path.write_text(text)
    later = before + 2_000_000_000
    os.utime(path, ns=(later, later))


def _run_on_pypi_hy(root: Path, guard: str, store: Path | None = None) -> int:
    """PyPI の Hy の使い捨ての環境で pkg.user を import し、展開された値を返す。共有の code の置き場は既定で root の下
    (検どうしで混ざらない)— 作業木をまたぐ検は同じ store を渡す。"""
    uv = shutil.which("uv")
    assert uv is not None, "uv が PATH に無い — PyPI の Hy の使い捨ての環境を作れない"
    script = root / f"load_{guard}.py"
    script.write_text(_LOADER.format(root=str(root), guard=guard, guard_root=str(GUARD_ROOT)))
    # 子はこの process の環境を継ぐ — _ENV_NOT_PASSED の名だけ外し(`env -u`)、共有の code の置き場を足す。
    unset = [flag for name in _ENV_NOT_PASSED for flag in ("-u", name)]
    code_store = store if store is not None else root / "code-store"
    python = f"{sys.version_info.major}.{sys.version_info.minor}"
    completed = subprocess.run(
        [
            "env",
            *unset,
            f"DOEFF_HY_CODE_STORE={code_store}",
            uv,
            "run",
            "--no-project",
            "--isolated",
            "--python",
            python,
            "--with",
            PYPI_HY,
            "python",
            str(script),
        ],
        capture_output=True,
        text=True,
        cwd=root,
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    hy_version, value = completed.stdout.split()
    assert hy_version == PYPI_HY.split("==")[1]
    return int(value)


def _user_pyc(root: Path) -> Path:
    [pyc] = (root / "pkg" / "__pycache__").glob("user.*.pyc")
    return pyc


def test_pypi_hy_alone_keeps_the_old_expansion(tmp_path: Path) -> None:
    """包みが無いと #1003 の赤がそのまま出る(この反例のテストが意味を持つことの確かめ)。"""
    _write_package(tmp_path)
    assert _run_on_pypi_hy(tmp_path, "none") == 11
    _edit(
        tmp_path / "pkg" / "macros.hy",
        "(import pkg.helpers [base])\n(defmacro answer [] (+ (base) 2))\n",
    )
    assert _run_on_pypi_hy(tmp_path, "none") == 11


@pytest.mark.parametrize("guard", ["before-hy", "after-hy"])
def test_a_macro_change_recompiles_the_user_on_pypi_hy(tmp_path: Path, guard: str) -> None:
    """macro を変えた後、使う側は新しい展開で動く — 包みを Hy の import の前に入れても(.pth)後に入れても。"""
    _write_package(tmp_path)
    assert _run_on_pypi_hy(tmp_path, guard) == 11
    _edit(
        tmp_path / "pkg" / "macros.hy",
        "(import pkg.helpers [base])\n(defmacro answer [] (+ (base) 2))\n",
    )
    assert _run_on_pypi_hy(tmp_path, guard) == 12
    assert _run_on_pypi_hy(tmp_path, guard) == 12


def test_a_change_of_the_helper_a_macro_calls_recompiles_the_user(tmp_path: Path) -> None:
    """macro が展開の時に呼ぶ同じ package の補助を変えても、使う側は作り直される。"""
    _write_package(tmp_path)
    assert _run_on_pypi_hy(tmp_path, "before-hy") == 11
    _edit(tmp_path / "pkg" / "helpers.hy", "(defn base [] 20)\n")
    assert _run_on_pypi_hy(tmp_path, "before-hy") == 21


def test_a_change_of_the_helper_a_macro_imports_inside_its_body_recompiles_the_user(
    tmp_path: Path,
) -> None:
    """macro が展開の時に関数の中で import する同じ package の補助を変えても、使う側は作り直される(agora-redesign #2373 —
    doeff-hy の defsystem は展開の中で ``doeff-hy.system-form`` を import する。その補助だけが変わった pin の後、提供元の
    名前空間に補助が無いので記録から漏れ、古い置き場を import する展開が .pyc に残った)。"""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "helpers.hy").write_text("(defn base [] 10)\n")
    (package / "macros.hy").write_text(
        "(defmacro answer [] (import pkg.helpers [base]) (+ (base) 1))\n"
    )
    (package / "user.hy").write_text("(require pkg.macros [answer])\n(setv value (answer))\n")
    assert _run_on_pypi_hy(tmp_path, "before-hy") == 11
    _edit(package / "helpers.hy", "(defn base [] 20)\n")
    assert _run_on_pypi_hy(tmp_path, "before-hy") == 21


def test_the_record_lives_inside_a_standard_pyc(tmp_path: Path) -> None:
    """記録は .pyc の中(code の定数)にあり、__pycache__ には標準の .pyc しか無い(.hydeps のような隣の file を書かない)。"""
    _write_package(tmp_path)
    _run_on_pypi_hy(tmp_path, "before-hy")
    cache = tmp_path / "pkg" / "__pycache__"
    assert sorted(path.suffix for path in cache.iterdir()) == [".pyc"] * 4
    data = _user_pyc(tmp_path).read_bytes()
    assert data[:4] == importlib.util.MAGIC_NUMBER
    record = records.record_of(marshal.loads(data[records.PYC_HEADER_BYTES :]))
    assert record is not None
    assert record.hy_version == PYPI_HY.split("==")[1]
    # 記録は展開が使った macro(提供元の module 名・macro 名・digest)で、file の path を持たない。
    assert [(used.table, used.module, used.name) for used in record.macros] == [
        ("_hy_macros", "pkg.macros", "answer")
    ]
    # 提供元の macro は answer だけなので、名を選んだ require でも全部の require と数える(古いと判じる側に倒れるだけ)。
    assert record.providers == (records.WholeRequire("pkg.macros", ""),)


def _rewrite_as_hash_based(pyc: Path, source: Path, *, checked: bool) -> None:
    """.pyc の頭を PEP 552 の hash 方式へ差し替える(image の組み立ての道具が焼く形 = unchecked)。"""
    data = pyc.read_bytes()
    flags = records.FLAG_HASH_BASED | (records.FLAG_CHECK_SOURCE if checked else 0)
    header = (
        importlib.util.MAGIC_NUMBER
        + flags.to_bytes(4, "little")
        + importlib.util.source_hash(source.read_bytes())
    )
    pyc.write_bytes(header + data[records.PYC_HEADER_BYTES :])


def test_a_checked_hash_pyc_is_recompiled_in_the_same_form(tmp_path: Path) -> None:
    """Python が source と突き合わせる hash 方式の .pyc も作り直し、同じ方式(checked-hash)で書き直す。"""
    _write_package(tmp_path)
    _run_on_pypi_hy(tmp_path, "before-hy")
    _rewrite_as_hash_based(_user_pyc(tmp_path), tmp_path / "pkg" / "user.hy", checked=True)
    _edit(
        tmp_path / "pkg" / "macros.hy",
        "(import pkg.helpers [base])\n(defmacro answer [] (+ (base) 2))\n",
    )
    assert _run_on_pypi_hy(tmp_path, "before-hy") == 12
    flags = int.from_bytes(_user_pyc(tmp_path).read_bytes()[4:8], "little")
    assert flags == records.FLAG_HASH_BASED | records.FLAG_CHECK_SOURCE


def test_an_unchecked_hash_pyc_is_trusted_like_python_trusts_it(tmp_path: Path) -> None:
    """Python が source と突き合わせない .pyc(image の形)は、macro の変更も突き合わせない — 組み立ての中で焼き直す前提。"""
    _write_package(tmp_path)
    _run_on_pypi_hy(tmp_path, "before-hy")
    _rewrite_as_hash_based(_user_pyc(tmp_path), tmp_path / "pkg" / "user.hy", checked=False)
    _edit(
        tmp_path / "pkg" / "macros.hy",
        "(import pkg.helpers [base])\n(defmacro answer [] (+ (base) 2))\n",
    )
    assert _run_on_pypi_hy(tmp_path, "before-hy") == 11


def _write_counting_package(root: Path, add: int) -> None:
    """_write_package と同じ 3 つだが、macro が展開のたびに root の expansions.log へ 1 行書く(変換をやり直したかを数える)。"""
    _write_package(root)
    (root / "pkg" / "macros.hy").write_text(
        "(import pkg.helpers [base])\n"
        '(defmacro answer [] (with [f (open "expansions.log" "a")] (.write f "x\\n"))'
        f" (+ (base) {add}))\n"
    )


def _expansions(root: Path) -> int:
    log = root / "expansions.log"
    return len(log.read_text().splitlines()) if log.exists() else 0


def test_a_new_tree_with_the_same_sources_reuses_the_code_without_expanding(tmp_path: Path) -> None:
    """新しい作業木(同じ中身・別の絶対 path・.pyc なし)は、共有の置き場の code を使い、macro を展開し直さない
    (agora-redesign #1753)。記録は file の path を持たないので、2 つの木の .pyc の記録は同じ。木の .pyc も書かれる。"""
    store = tmp_path / "store"
    first, second = tmp_path / "first", tmp_path / "second"
    for root in (first, second):
        root.mkdir()
        _write_counting_package(root, 1)
    assert _run_on_pypi_hy(first, "before-hy", store) == 11
    assert _expansions(first) == 1
    assert _run_on_pypi_hy(second, "before-hy", store) == 11
    assert _expansions(second) == 0
    record = records.record_of(
        marshal.loads(_user_pyc(second).read_bytes()[records.PYC_HEADER_BYTES :])
    )
    assert record is not None
    assert record == records.record_of(
        marshal.loads(_user_pyc(first).read_bytes()[records.PYC_HEADER_BYTES :])
    )


def test_a_new_tree_with_a_different_macro_does_not_reuse_the_other_trees_expansion(
    tmp_path: Path,
) -> None:
    """同じ使い手でも macro の中身が違う木は、別の木で作った code を使わない — 記録を作った木の file ではなく、今の木の
    提供元で照らす(作った木の macro が変わらず残っていても)。"""
    store = tmp_path / "store"
    first, other = tmp_path / "first", tmp_path / "other"
    first.mkdir()
    other.mkdir()
    _write_counting_package(first, 1)
    _write_counting_package(other, 5)
    assert _run_on_pypi_hy(first, "before-hy", store) == 11
    assert _run_on_pypi_hy(other, "before-hy", store) == 15
    assert _expansions(other) == 1


def test_a_pyc_carried_from_another_tree_is_checked_against_the_current_trees_macro(
    tmp_path: Path,
) -> None:
    """実行環境の準備は、前の root の .pyc を source の hash が同じ file について hardlink で引き継ぐ。引き継いだ .pyc の記録は
    前の root の macro の file を名指すので、前の root の macro が変わらず残っていても、今の木の macro が違えば作り直す
    (agora-redesign #2598)。記録の path のまま照らすと、前の root の展開(11)を使い続ける。"""
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _write_counting_package(first, 1)
    _write_counting_package(second, 5)
    assert _run_on_pypi_hy(first, "before-hy", tmp_path / "store-first") == 11
    carried = _user_pyc(first)
    _rewrite_as_hash_based(carried, first / "pkg" / "user.hy", checked=True)
    cache = second / "pkg" / "__pycache__"
    cache.mkdir()
    os.link(carried, cache / carried.name)
    assert _run_on_pypi_hy(second, "before-hy", tmp_path / "store-second") == 15
    assert _expansions(second) == 1


def test_a_file_changed_between_compile_and_the_store_write_does_not_file_old_code_under_the_new_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """共有の置き場の鍵は、compile した bytes そのものから作る(agora-redesign #2799)。以前は compile の後に file を読み直して
    鍵を作ったので、その間に file が書き換わると(着地の git の更新・編集の最中の import)、古い中身から作った code が新しい
    中身の鍵の下に入り、同じ中身の file を読む全部の作業木が古い振る舞いで動いた(実例 2026-10-02 13:57 — doeff-cluster の
    sim/local.hy の検が毎回赤)。"""
    import hy  # noqa: F401 — Hy の source を compile する口を載せる
    from doeff_hy_bytecode_guard import code_store, loader_hooks

    store = tmp_path / "store"
    monkeypatch.setenv(code_store.STORE_ENV, str(store))
    # この suite は PYTHONDONTWRITEBYTECODE=1 で走る — 置き場は bytecode を書く設定の時だけ書くので、この検の中だけ書かせる。
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    package = tmp_path / "racepkg"
    package.mkdir()
    source = package / "mod.hy"
    old, new = b"(setv value 1)\n", b"(setv value 2)\n"
    source.write_bytes(old)
    name = "racepkg.mod"

    class FileChangedAfterCompile(importlib.machinery.SourceFileLoader):
        """compile の直後に source の file が書き換わった筋書き — compile した code の .pyc を書いた後の source の読みは
        新しい中身を返す(標準の get_code は compile の直後に .pyc を書く)。"""

        mut_compiled = False

        def set_data(self, path: str, data: Buffer, *, _mode: int = 0o666) -> None:
            super().set_data(path, data, _mode=_mode)
            self.mut_compiled = True

        def get_data(self, path: str) -> bytes:
            if self.mut_compiled and path == str(source):
                return new
            return super().get_data(path)

    loader = FileChangedAfterCompile(name, str(source))
    spec = importlib.util.spec_from_file_location(name, str(source), loader=loader)
    assert spec is not None
    monkeypatch.setitem(sys.modules, name, importlib.util.module_from_spec(spec))
    assert loader.get_code(name) is not None
    assert loader.mut_compiled
    assert not Path(loader_hooks._store_entry(str(store), name, str(source), new)).exists(), (
        "新しい中身の鍵の下に、古い中身から作った code が入った"
    )
    assert Path(loader_hooks._store_entry(str(store), name, str(source), old)).exists(), (
        "compile した中身の鍵で置き場に入らない"
    )


def test_the_venv_installs_the_guard_at_startup_before_hy() -> None:
    """doeff-hy を入れた venv では、どの Python も起動の時点(Hy の import より前)で包みが入っている(.pth)。"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, doeff_hy_bytecode_guard as g; print(g.installed(), 'hy' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.split() == ["True", "False"]


def test_a_provider_first_loaded_by_the_type_check_expansion_is_not_reused_by_a_normal_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """型検査のための展開(doeff_hy.static_view)の最中に require で初めて読み込まれた macro の提供元は、:pre の isinstance と
    実行の時の import の無い、実行できない code になる。それに macro の依存の記録を付けると、.pyc と共有の置き場に普通の展開と
    同じ鍵で残り、後の普通の import がそれを読んで ``NameError: name '_doeff_do' is not defined`` で落ちた(milestone I の I-3 の
    再現 2026-10-03 — doeff-hy-check の後の普通の実行)。型検査の展開の code は置き場に入らず、後の普通の import は :pre の付いた
    展開を読む。"""
    import hy  # noqa: F401 — Hy の source を compile する口を載せる
    from doeff import run
    from doeff_hy.static_view import static_view
    from doeff_hy_bytecode_guard import code_store, loader_hooks

    store = tmp_path / "store"
    monkeypatch.setenv(code_store.STORE_ENV, str(store))
    # この suite は PYTHONDONTWRITEBYTECODE=1 で走る — .pyc と置き場は bytecode を書く設定の時だけ書くので、この検の中だけ書かせる。
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    monkeypatch.setattr(sys, "pycache_prefix", str(tmp_path / "pyc"))
    package = tmp_path / "typecheckpkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    source = package / "provider.hy"
    source.write_text(
        "(require doeff-hy.macros [defk])\n"
        "(defmacro twice [x] `(+ ~x ~x))\n"
        "(defk checked [x]\n"
        '  {:pre [(: x int)] :post [(: % int)] :tags {:context "probe" :role "judgment"}}\n'
        '  "型の契約つきの関数(:pre の isinstance は普通の展開にだけ在る)。"\n'
        "  x)\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    name = "typecheckpkg.provider"
    try:
        # 型検査の対象の require が初めて読み込む形の compile の段(module を置いた中の get_code — .pyc と置き場への書きが起きる)。
        # 型検査の展開の code は実行できないので exec はしない(doeff-hy-check でも require の exec が NameError で落ちる)。
        spec = importlib.util.find_spec(name)
        assert spec is not None and isinstance(spec.loader, importlib.machinery.SourceFileLoader)
        sys.modules[name] = importlib.util.module_from_spec(spec)
        with static_view():
            spec.loader.get_code(name)
        assert not Path(loader_hooks._store_entry(str(store), name, str(source), source.read_bytes())).exists(), (
            "型検査のための展開の code が共有の置き場に入った"
        )
        del sys.modules[name]
        provider = importlib.import_module(name)  # 同じ .pyc の置き場と共有の置き場での普通の import
        with pytest.raises(AssertionError, match="pre-condition"):
            run(provider.checked("not-an-int"))
    finally:
        for loaded in (name, "typecheckpkg"):
            sys.modules.pop(loaded, None)
