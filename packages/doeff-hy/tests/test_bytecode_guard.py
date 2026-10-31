"""macro が変わった Hy の module の古い bytecode を使わない包み(doeff_hy_bytecode_guard)の反例。

#1003 の形: macro の提供元を変えても、使う側の module の .pyc は source の更新時刻と大きさしか見ない Python の判定で
有効とされ、古い展開のまま動く。包みがあれば、使う側は新しい展開で動く。

Hy は **PyPI の版**(fork ではない — Hy は改めない)を、この venv と切り離した
使い捨ての環境(``uv run --no-project --isolated --with hy==<uv.lock の版>``)で使う。この venv は起動の時に .pth から
包みを入れる(test_the_venv_installs_the_guard_at_startup_before_hy)ので、ここでは包みの無い形と分けて測れない。
版は repo の根の uv.lock の hy の行から、doeff で uv.lock の版を読む 1 か所(scripts/semgrep_locked.py の locked_version)で読む。
写しの定数だと、uv.lock の Hy を上げてもこの検は古い版のまま測る。uv.lock の変更では
`make test-changed` がこの file を選ぶ(root の pyproject.toml の [[tool.doeff.contract-tests]])。

包みは import でない形でも Hy の内部に依り、この file がその版の Hy で包みを動かして測る(ADR-DOE-HY-008 の
R7 の母集団の外): module の macro の表の名(``records.MACRO_TABLE``)と Hy の展開がその表を ``名 in 表`` と ``表[名]`` で引く事
(引いた名と表に無かった名を覚える表が拾う — macro の変更の検・使う側の呼ぶ名の macro が足された時の検)・reader macro の表の名
(``records.READER_TABLE`` — reader macro の変更の検)・Hy の export が書く公開の macro の名の表(全部の require の判定が読む)・
gensym と let が改めた束縛の名の形(compile の口の正準化が読む — gensym の数えの検)。どれかが替わると、包みは使った macro を
記録し損ねて古い展開を黙って使うか、code が compile の順で揺れる。

Hy の内部の名の定義元は ``records`` の 1 か所(``MACRO_TABLE``・``READER_TABLE``・``EXPORT_NAMES``・``GENERATED_NAME_PREFIX``・
``ANON_VAR_NAME``)で、包みの外の使い手(doeff_effect_analyzer._result_cache の解析の鍵・doeff_hy._static_baseline の基点の鍵・
macro_use の全部の require の判定)もそこから引く。この file の末尾の 2 本が、records の読みを実物の Hy の
module に当てる: 表と export の名の読みは Hy 自身の ``(require 提供元 *)`` の振る舞いと一致し、Hy が一時の名に振る形は
``fold_anon_var_numbers`` が畳める形のまま。Hy が名を替えるとここが赤になる。字が records の外へ写される事は
test_hy_internal_names_have_one_home.py の走査が赤にする。

同じ process で包みを動かす検(置き場・口の包み・型検査の展開)は venv の Hy を測る。venv の Hy が uv.lock の版でない作業木(Hy を
上げて sync していない)では、どの検も uv.lock の版を測らずに緑になりうるので、file の全部の検が先に版の一致を確かめる
(:func:`_venv_hy_is_the_locked_version`・B)。PyPI の版の中身は版ごとに変わらない
ので、版が同じなら同じ Hy を測る。Hy の内部への依りを包みに足す便は、その振る舞いの検をこの file に置く。
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import marshal
import os
import shutil
import subprocess
import sys
import types
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from doeff_hy_bytecode_guard import _records as records

if TYPE_CHECKING:
    # collections.abc.Buffer は Python 3.12 から — 注記にだけ使うので、3.11 で module の読み込みを落とさない。
    from collections.abc import Buffer

#: 測る Hy の版を決める uv.lock を持つ repo の根と、doeff で uv.lock の版を読む 1 か所。
ROOT = Path(__file__).resolve().parents[3]
LOCK_READER = ROOT / "scripts" / "semgrep_locked.py"
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


@cache
def _locked_hy_version() -> str:
    """repo の根の uv.lock が決める Hy の版。hy の行が 1 つでない・index から入れる版でない(フォークの git など)時は、読み手が
    理由を名指して止まる(SystemExit — 測る版を決められない)。"""
    spec = importlib.util.spec_from_file_location("semgrep_locked", LOCK_READER)
    assert spec is not None
    assert spec.loader is not None
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    return reader.locked_version(ROOT, "hy")


@pytest.fixture(autouse=True)
def _venv_hy_is_the_locked_version() -> None:
    """この file の検は uv.lock の版の Hy で包みを測る — 同じ process の検が測る venv の Hy も、その版である事を先に確かめる。"""
    import hy

    assert hy.__version__ == _locked_hy_version(), (
        f"venv の Hy {hy.__version__} が uv.lock の版 {_locked_hy_version()} でない — 同じ process の検が uv.lock の版を"
        " 測らないので、`make sync` してから測る"
    )


_LOADER = """\
import sys
sys.path.insert(0, {root!r})
guard = {guard!r}


def load_guard():
    # 包みの package は checkout の中にあるので、その .pyc を checkout に書かない(root の conftest の検査)—
    # 包みの module を全部先に読んでおく(包みは一部の module を Hy の source に初めて当たった時に読む。名を並べて読むと、
    # 後から足した module(expansion_trees・macro_hints)が書く設定の下で初めて読まれ、その .pyc が checkout に書かれた)。
    import importlib
    import pkgutil
    sys.dont_write_bytecode = True
    sys.path.insert(0, {guard_root!r})
    import doeff_hy_bytecode_guard
    for module in pkgutil.iter_modules(doeff_hy_bytecode_guard.__path__, "doeff_hy_bytecode_guard."):
        importlib.import_module(module.name)
    doeff_hy_bytecode_guard.install()
    sys.dont_write_bytecode = False


if guard == "before-hy":
    load_guard()
import hy
if guard == "after-hy":
    load_guard()
print(hy.__version__)
{probe}
"""

#: 包みを入れた後に使い捨ての環境で走らせる確かめの既定 — 使う側を import し、展開された値を出す。
_IMPORT_USER = "import pkg.user\nprint(pkg.user.value)\n"

#: 使う側を同じ process で 2 度 compile し(包みの compile の口 — import と同じく記録を足し gensym の名を振り直す)、間で Hy の
#: gensym の数えを進める確かめ。答えは 2 つの code が同じか(code の == — 命令・定数・名。marshal の bytes は object の参照の数で
#: 印が変わるので比べない)と、片方にだけ在る名と文字列。提供元は先に読む
#: (1 度目の compile だけが提供元の読み込みを囲むと、記録の印が 2 つで違いうる)。
_COMPILE_TWICE = """\
import importlib
import importlib.util
import json
import types
from doeff_hy_bytecode_guard._loader_hooks import source_to_code_as_import

importlib.import_module("pkg.macros")
spec = importlib.util.find_spec("pkg.user")


def compiled():
    with open(spec.origin, "rb") as source:
        return source_to_code_as_import(spec.loader, source.read(), spec.origin)


def texts(code):
    yield from (*code.co_names, *code.co_varnames, *code.co_cellvars, *code.co_freevars)
    for constant in code.co_consts:
        if isinstance(constant, types.CodeType):
            yield from texts(constant)
        elif isinstance(constant, str):
            yield constant


first = compiled()
for _ in range(100):
    hy.gensym()
second = compiled()
print(json.dumps({"same": first == second,
                  "differing": sorted(set(texts(first)) ^ set(texts(second)))}))
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


def _run_on_pypi_hy(root: Path, guard: str, store: Path | None = None, guard_root: Path = GUARD_ROOT) -> int:
    """PyPI の Hy の使い捨ての環境で pkg.user を import し、展開された値を返す。共有の code の置き場は既定で root の下
    (検どうしで混ざらない)— 作業木をまたぐ検は同じ store を渡す。guard_root = 包みの package の親(既定はこの checkout の doeff-hy の
    src — 版の違う包みの代役を渡す検がある)。"""
    return int(_probe_on_pypi_hy(root, guard, _IMPORT_USER, store, guard_root))


def _probe_on_pypi_hy(
    root: Path, guard: str, probe: str, store: Path | None = None, guard_root: Path = GUARD_ROOT
) -> str:
    """PyPI の Hy の使い捨ての環境で、包みを入れた後に probe(Python の文 — 答えを stdout へ出す)を走らせ、答えの文を返す。
    置き場と guard_root は :func:`_run_on_pypi_hy` と同じ。"""
    uv = shutil.which("uv")
    assert uv is not None, "uv が PATH に無い — PyPI の Hy の使い捨ての環境を作れない"
    script = root / f"load_{guard}.py"
    script.write_text(
        _LOADER.format(root=str(root), guard=guard, guard_root=str(guard_root), probe=probe)
    )
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
            f"hy=={_locked_hy_version()}",
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
    hy_version, answer = completed.stdout.split(maxsplit=1)
    assert hy_version == _locked_hy_version()
    return answer


def _user_pyc(root: Path) -> Path:
    [pyc] = (root / "pkg" / "__pycache__").glob("user.*.pyc")
    return pyc


def _stored_entries(store: Path | str, name: str, source: Path, data: bytes) -> tuple[str, ...]:
    """共有の置き場の、module 名 name の source(中身 data)の code の鍵の entry(macro の環境ごとに並ぶ — 使った時刻の新しい順)。"""
    from doeff_hy_bytecode_guard import _code_store as code_store, _loader_hooks as loader_hooks

    return code_store.code_entries(str(store), loader_hooks._store_key(name, str(source), data))


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
    """macro が展開の時に関数の中で import する同じ package の補助を変えても、使う側は作り直される(
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


@pytest.mark.parametrize(("guard", "after"), [("none", 10), ("before-hy", 15)])
def test_a_reader_macro_change_recompiles_the_user_on_pypi_hy(
    tmp_path: Path, guard: str, after: int
) -> None:
    """reader macro を変えた後、使う側は新しい展開で動く(包みが無いと古い展開のまま — この検が包みを測る事の確かめ)。包みは
    使う側の module の reader macro の表(``records.READER_TABLE``)に在る他の module の reader macro を記録に入れるので、Hy が表の
    名を替えると記録から漏れ、古い展開を黙って使う。"""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    reader = "(defreader twice\n  (setv x (.parse-one-form &reader))\n  `(* {} ~x))\n"
    (package / "readers.hy").write_text(reader.format(2))
    (package / "user.hy").write_text(
        "(require pkg.readers :readers [twice])\n(setv value #twice 5)\n"
    )
    assert _run_on_pypi_hy(tmp_path, guard) == 10
    _edit(package / "readers.hy", reader.format(3))
    assert _run_on_pypi_hy(tmp_path, guard) == after


@pytest.mark.parametrize(("guard", "after"), [("none", 11), ("before-hy", 12), ("after-hy", 12)])
def test_a_macro_required_inside_a_function_recompiles_the_user_on_pypi_hy(
    tmp_path: Path, guard: str, after: int
) -> None:
    """関数の中の require で入れた macro を変えた後も、使う側は新しい展開で動く(包みが無いと古い展開のまま — この検が包みを
    測る事の確かめ)。Hy の展開は関数の中の局所の macro の表(compiler の局所の状態)を module の表より先に引くので、module の表を
    替えるだけの包みは局所の macro を記録から漏らし、macro を変えても古い展開を黙って使った。
    包みは compiler が局所の状態を開く口を読むので、Hy がその口を替えると
    この検が赤になる。"""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "macros.hy").write_text("(defmacro answer [] 11)\n")
    (package / "user.hy").write_text(
        "(defn f []\n  (require pkg.macros [answer])\n  (defn g [] (answer))\n  (g))\n(setv value (f))\n"
    )
    assert _run_on_pypi_hy(tmp_path, guard) == 11
    _edit(package / "macros.hy", "(defmacro answer [] 12)\n")
    assert _run_on_pypi_hy(tmp_path, guard) == after
    assert _run_on_pypi_hy(tmp_path, guard) == after


@pytest.mark.parametrize("exports", ["", "(export :macros [answer])\n"], ids=["all", "exported"])
def test_a_new_macro_named_like_a_function_the_user_calls_recompiles_the_user_on_pypi_hy(
    tmp_path: Path, exports: str
) -> None:
    """全部を require する使う側が呼ぶ関数と同じ名の macro が提供元に足されると、使う側は作り直され、呼び出しが macro の展開に
    変わる。包みは展開が表に引いて無かった名(``名 in 表`` が偽)を覚え、提供元に足されたら古いと判じるので、Hy が表を ``in`` で
    確かめなくなると覚え損ねる。提供元が Hy の export で公開の macro を絞る形(exported)では、全部の require の判定が Hy の公開の
    名の表を読むので、Hy がその名を替えると全部の require と数えず、足された macro を見逃す(
    B)。"""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    provider = exports + "(defmacro answer [] 1)\n(defmacro hidden [] 2)\n"
    (package / "macros.hy").write_text(provider)
    (package / "user.hy").write_text(
        "(require pkg.macros *)\n(defn shadow [x] x)\n(setv value (+ (answer) (shadow 10)))\n"
    )
    assert _run_on_pypi_hy(tmp_path, "before-hy") == 11
    _edit(
        package / "macros.hy",
        provider.replace("[answer]", "[answer shadow]") + "(defmacro shadow [x] 0)\n",
    )
    assert _run_on_pypi_hy(tmp_path, "before-hy") == 1


def test_the_code_does_not_depend_on_how_many_gensyms_the_process_made_on_pypi_hy(
    tmp_path: Path,
) -> None:
    """同じ source の code は、process が先に何度 gensym を呼んだかに依らない — compile の口が gensym の名(let が改めた束縛の名に
    埋まった物も)を module の中の順へ振り直す。振り直しは Hy の gensym と let の名の形を読むので、Hy が
    形を替えると黙って振り直さず、.pyc・共有の置き場・詰めた Program の指紋が compile の順で揺れる。"""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "macros.hy").write_text(
        '(defmacro bound [x]\n  (setv g (hy.gensym "bound"))\n  `(let [~g ~x] (+ ~g 1)))\n'
    )
    (package / "user.hy").write_text(
        "(require pkg.macros [bound])\n(defn f [] (bound 10))\n(setv value (f))\n"
    )
    answer = json.loads(_probe_on_pypi_hy(tmp_path, "before-hy", _COMPILE_TWICE))
    assert answer == {"same": True, "differing": []}


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
    assert record.hy_version == _locked_hy_version()
    # 記録は展開が使った macro(提供元の module 名・macro 名・digest)で、file の path を持たない。
    assert [(used.table, used.module, used.name) for used in record.macros] == [
        (records.MACRO_TABLE, "pkg.macros", "answer")
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


def _counting_macros(add: int) -> str:
    """macro が展開のたびに root の expansions.log へ 1 行書く macros.hy の中身(変換をやり直したかを数える)。"""
    return (
        "(import pkg.helpers [base])\n"
        '(defmacro answer [] (with [f (open "expansions.log" "a")] (.write f "x\\n"))'
        f" (+ (base) {add}))\n"
    )


def _write_counting_package(root: Path, add: int) -> None:
    """_write_package と同じ 3 つだが、macro が展開のたびに root の expansions.log へ 1 行書く(変換をやり直したかを数える)。"""
    _write_package(root)
    (root / "pkg" / "macros.hy").write_text(_counting_macros(add))


def _expansions(root: Path) -> int:
    log = root / "expansions.log"
    return len(log.read_text().splitlines()) if log.exists() else 0


def test_a_new_tree_with_the_same_sources_reuses_the_code_without_expanding(tmp_path: Path) -> None:
    """新しい作業木(同じ中身・別の絶対 path・.pyc なし)は、共有の置き場の code を使い、macro を展開し直さない。
   記録は file の path を持たないので、2 つの木の .pyc の記録は同じ。木の .pyc も書かれる。"""
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


def test_two_macro_versions_taking_turns_on_one_store_stop_expanding_from_the_second_round(tmp_path: Path) -> None:
    """macro の中身の違う 2 つの書き手(doeff の版の違う会話の作業木と実行環境の準備の代役 — 使い手の source は同じ)が、同じ置き場で
    新しい木を交互に読む。鍵 1 つに entry 1 つの並びでは、互いの entry を古いと見て展開し直し、同じ鍵を上書きし合った(2026-10-12
    zeus — 準備 1 回に 100〜600 本の焼き直し)。鍵 1 つに macro の環境ごとの entry を持つ並びでは、2 巡目から
    どちらも展開し直さない。"""
    store = tmp_path / "store"
    for turn in (1, 2, 3):
        for name, add in (("one", 1), ("five", 5)):
            root = tmp_path / f"{name}-{turn}"
            root.mkdir()
            _write_counting_package(root, add)
            assert _run_on_pypi_hy(root, "before-hy", store) == 10 + add
            assert _expansions(root) == (1 if turn == 1 else 0), (name, turn)


def _guard_of_the_next_record_generation(tmp_path: Path) -> Path:
    """この checkout の包みの写しで、記録の印(RECORD_TAG)だけを次の世代へ上げた物の親の dir — doeff-hy の版の違う書き手の代役(記録の
    形の世代が違う書き手どうしは、互いの entry の記録を読めない)。"""
    root = tmp_path / "guard-next-generation"
    shutil.copytree(
        GUARD_ROOT / "doeff_hy_bytecode_guard",
        root / "doeff_hy_bytecode_guard",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    records_file = root / "doeff_hy_bytecode_guard" / "_records.py"
    current = f'RECORD_TAG = "{records.RECORD_TAG}"'
    text = records_file.read_text(encoding="utf-8")
    assert text.count(current) == 1, "記録の印の行が records.py に 1 つでない — 次の世代の代役を作れない"
    prefix, number = records.RECORD_TAG.rsplit("/", 1)
    records_file.write_text(text.replace(current, f'RECORD_TAG = "{prefix}/{int(number) + 1}"'), encoding="utf-8")
    return root


def test_two_record_generations_taking_turns_on_one_store_keep_each_others_entries(tmp_path: Path) -> None:
    """記録の印(RECORD_TAG)の違う 2 つの書き手(doeff-hy の版の違う作業木と実行環境の準備の代役 — 互いの entry の記録を読めない)が、
    同じ置き場で新しい木を交互に読む。読めない世代の entry を読み手が「古い」として除くと、互いの entry を消し合って毎回展開し直す
    (2 つの書き手の doeff-hy が同じ検では見分けられない)。鍵 1 つに環境ごとの entry を
    持つ並びでは、どちらも 2 巡目から展開し直さない。"""
    store = tmp_path / "store"
    writers = (("this", GUARD_ROOT), ("next", _guard_of_the_next_record_generation(tmp_path)))
    for turn in (1, 2, 3):
        for name, guard_root in writers:
            root = tmp_path / f"{name}-{turn}"
            root.mkdir()
            _write_counting_package(root, 1)
            assert _run_on_pypi_hy(root, "before-hy", store, guard_root) == 11
            assert _expansions(root) == (1 if turn == 1 else 0), (name, turn)


def _user_entries(store: Path, root: Path) -> tuple[str, ...]:
    """置き場の、root の木の pkg.user の鍵の entry(macro の環境ごとに並ぶ)。"""
    user = root / "pkg" / "user.hy"
    return _stored_entries(store, "pkg.user", user, user.read_bytes())


def test_a_stale_pyc_in_a_reused_tree_takes_the_stores_matching_entry_without_compiling(tmp_path: Path) -> None:
    """使い回す作業木の .pyc は、頭(時刻と大きさ)が source に合い、記録だけが今の環境の macro に合わない(兄弟の doeff を替えた木 — 配備の
    宣言の木が宣言ごとに doeff を替える)。置き場に今の環境で合う entry が在るなら、compile(= 展開)し直さずそれを使い、.pyc も書き直す。
    引かないと最初の import が木の Hy の module を全部 compile し直す(2026-10-12 — 検 1 本の収集で約 880 本・CPU 約 670 秒)。"""
    store = tmp_path / "store"
    reused, other = tmp_path / "reused", tmp_path / "other"
    for root, add in ((reused, 1), (other, 5)):
        root.mkdir()
        _write_counting_package(root, add)
    assert _run_on_pypi_hy(reused, "before-hy", store) == 11
    assert _run_on_pypi_hy(other, "before-hy", store) == 15  # 置き場に add = 5 の環境の entry が足される
    assert (_expansions(reused), _expansions(other)) == (1, 1)
    # 使い回す木の macro の環境が other と同じになる — user.hy の .pyc の頭は合ったまま、記録だけが古くなる。
    _edit(reused / "pkg" / "macros.hy", _counting_macros(5))
    assert _run_on_pypi_hy(reused, "before-hy", store) == 15
    assert _expansions(reused) == 1  # 置き場の entry を使った — 展開し直していない
    # .pyc は置き場の code で書き直された — 空の置き場でも展開し直さない。
    assert _run_on_pypi_hy(reused, "before-hy", tmp_path / "empty-store") == 15
    assert _expansions(reused) == 1


def test_a_stale_checked_hash_pyc_takes_the_stores_matching_entry_and_is_rewritten_in_the_same_form(
    tmp_path: Path,
) -> None:
    """Python が source と突き合わせる hash 方式の .pyc も、記録が古ければ置き場の合う entry を使い、同じ方式(checked-hash)で書き直す。"""
    store = tmp_path / "store"
    reused, other = tmp_path / "reused", tmp_path / "other"
    for root, add in ((reused, 1), (other, 5)):
        root.mkdir()
        _write_counting_package(root, add)
    assert _run_on_pypi_hy(reused, "before-hy", store) == 11
    assert _run_on_pypi_hy(other, "before-hy", store) == 15
    _rewrite_as_hash_based(_user_pyc(reused), reused / "pkg" / "user.hy", checked=True)
    _edit(reused / "pkg" / "macros.hy", _counting_macros(5))
    assert _run_on_pypi_hy(reused, "before-hy", store) == 15
    assert _expansions(reused) == 1
    flags = int.from_bytes(_user_pyc(reused).read_bytes()[4:8], "little")
    assert flags == records.FLAG_HASH_BASED | records.FLAG_CHECK_SOURCE
    assert _run_on_pypi_hy(reused, "before-hy", tmp_path / "empty-store") == 15
    assert _expansions(reused) == 1


def test_a_stale_pyc_with_no_matching_entry_in_the_store_compiles_and_adds_the_entry(tmp_path: Path) -> None:
    """置き場に今の環境で合う entry が無い時は、今どおり compile し直し、その code を置き場へ足す(次に同じ環境の木が引ける)。"""
    store = tmp_path / "store"
    reused, other = tmp_path / "reused", tmp_path / "other"
    reused.mkdir()
    other.mkdir()
    _write_counting_package(reused, 1)
    _write_counting_package(other, 7)
    assert _run_on_pypi_hy(reused, "before-hy", store) == 11
    before = _user_entries(store, reused)
    _edit(reused / "pkg" / "macros.hy", _counting_macros(7))  # 置き場に add = 7 の entry は無い
    assert _run_on_pypi_hy(reused, "before-hy", store) == 17
    assert _expansions(reused) == 2  # compile し直した
    assert len(_user_entries(store, reused)) == len(before) + 1
    assert _run_on_pypi_hy(other, "before-hy", store) == 17  # 足した entry を同じ環境の別の木が使う
    assert _expansions(other) == 0


def test_a_pyc_carried_from_another_tree_is_checked_against_the_current_trees_macro(
    tmp_path: Path,
) -> None:
    """実行環境の準備は、前の root の .pyc を source の hash が同じ file について hardlink で引き継ぐ。引き継いだ .pyc の記録は
    前の root の macro の file を名指すので、前の root の macro が変わらず残っていても、今の木の macro が違えば作り直す。
   記録の path のまま照らすと、前の root の展開(11)を使い続ける。"""
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
    """共有の置き場の鍵は、compile した bytes そのものから作る。以前は compile の後に file を読み直して
    鍵を作ったので、その間に file が書き換わると(着地の git の更新・編集の最中の import)、古い中身から作った code が新しい
    中身の鍵の下に入り、同じ中身の file を読む全部の作業木が古い振る舞いで動いた(実例 2026-10-02 13:57 — doeff-cluster の
    sim/local.hy の検が毎回赤)。"""
    import hy  # noqa: F401 — Hy の source を compile する口を載せる
    from doeff_hy_bytecode_guard import _code_store as code_store

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
    assert not _stored_entries(store, name, source, new), (
        "新しい中身の鍵の下に、古い中身から作った code が入った"
    )
    assert _stored_entries(store, name, source, old), (
        "compile した中身の鍵で置き場に入らない"
    )


def _compile_in_fresh_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, package_name: str) -> Path:
    """tmp_path の下に Hy の module `<package_name>.mod` を 1 つ作り、書かない設定(この suite の固定)のまま読みの口で compile して、
    その source の path を返す。"""
    package = tmp_path / package_name
    package.mkdir()
    source = package / "mod.hy"
    source.write_bytes(b"(setv value 3)\n")
    name = f"{package_name}.mod"
    loader = importlib.machinery.SourceFileLoader(name, str(source))
    spec = importlib.util.spec_from_file_location(name, str(source), loader=loader)
    assert spec is not None
    monkeypatch.setitem(sys.modules, name, importlib.util.module_from_spec(spec))
    assert loader.get_code(name) is not None
    return source


def test_a_named_store_is_filled_even_when_bytecode_is_not_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """置き場の dir を DOEFF_HY_CODE_STORE で名指した時は、木へ .pyc を書かない設定でも置き場へは足す。
    worker の bytecode を焼く道具は PYTHONDONTWRITEBYTECODE=1 で起きるので、足さないと自分の依存の Hy を起動のたびに source から
    変換し直した(空の置き場で 2 回起こして 2 回とも CPU 約 14〜17 秒・置き場の file 0 — 2026-10-08)。"""
    import hy  # noqa: F401 — Hy の source を compile する口を載せる
    from doeff_hy_bytecode_guard import _code_store as code_store

    store = tmp_path / "store"
    monkeypatch.setenv(code_store.STORE_ENV, str(store))
    assert sys.dont_write_bytecode, "この検は書かない設定(suite の固定)の下で見る"
    name = "namedstorepkg.mod"
    source = _compile_in_fresh_package(tmp_path, monkeypatch, "namedstorepkg")
    assert _stored_entries(store, name, source, source.read_bytes()), (
        "名指しの置き場に、書かない設定で compile した code が入らない"
    )
    assert not (source.parent / "__pycache__").exists(), "書かない設定なのに木へ .pyc を書いた"


def test_the_default_store_is_not_filled_when_bytecode_is_not_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """置き場を名指していない時(既定の利用者の cache の dir)は、書かない設定では今までどおり置き場へ足さない — 書かない設定で
    起こした process が、名指しの無いまま利用者の cache を太らせない。"""
    import hy  # noqa: F401 — Hy の source を compile する口を載せる
    from doeff_hy_bytecode_guard import _code_store as code_store

    monkeypatch.delenv(code_store.STORE_ENV, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    store = code_store.store_dir()
    assert store is not None
    name = "defaultstorepkg.mod"
    source = _compile_in_fresh_package(tmp_path, monkeypatch, "defaultstorepkg")
    assert not _stored_entries(store, name, source, source.read_bytes())


def test_the_venv_installs_the_guard_at_startup_before_hy() -> None:
    """doeff-hy を入れた venv では、どの Python も起動の時点(Hy の import より前)で包みが入っている(.pth)。"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from doeff_hy_bytecode_guard._loader_hooks import installed;"
            " print(installed(), 'hy' in sys.modules)",
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
    """型検査のための展開(doeff_hy._static_view)の最中に require で初めて読み込まれた macro の提供元は、:pre の isinstance と
    実行の時の import の無い、実行できない code になる。それに macro の依存の記録を付けると、.pyc と共有の置き場に普通の展開と
    同じ鍵で残り、後の普通の import がそれを読んで ``NameError: name '_doeff_do' is not defined`` で落ちた(milestone I の I-3 の
    再現 2026-10-03 — doeff-hy-check の後の普通の実行)。型検査の展開の code は置き場に入らず、後の普通の import は :pre の付いた
    展開を読む。"""
    import hy  # noqa: F401 — Hy の source を compile する口を載せる
    from doeff import run
    from doeff_hy._static_view import static_view
    from doeff_hy_bytecode_guard import _code_store as code_store

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
        assert not _stored_entries(store, name, source, source.read_bytes()), (
            "型検査のための展開の code が共有の置き場に入った"
        )
        del sys.modules[name]
        provider = importlib.import_module(name)  # 同じ .pyc の置き場と共有の置き場での普通の import
        with pytest.raises(AssertionError, match="pre-condition"):
            run(provider.checked("not-an-int"))
    finally:
        for loaded in (name, "typecheckpkg"):
            sys.modules.pop(loaded, None)


def test_a_file_loaded_again_by_path_under_another_name_records_the_macros_its_expansion_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同じ Hy の file を package の名で読んだ process で、path から別の名で読み直す(worker の bytecode の道具が判断の module
    bake_plan.hy を読む形)と、Hy は compile の module を file の名から引く(``inspect.getmodule``)ので、展開は先に読んだ
    package の名の module の表で走る。記録を別の名の module の表から作ると、表は一度も引かれず、使った macro の無い空の記録が
    共有の置き場に入り、macro が変わった後も照らしが素通りで古い展開が使われた(2026-10-11 atlas — doeff の取り込みの検証の
    pytest が書いた bake_plan.hy の entry を patrol の準備が引き、ec08cd77d より前の defrecord の展開で
    ``seal_record() missing ... calls_post_init``)。記録は展開が実際に引いた表から作る。"""
    import hy  # noqa: F401 — Hy の source を compile する口を載せる
    from doeff_hy_bytecode_guard import _code_store as code_store

    store = tmp_path / "store"
    monkeypatch.setenv(code_store.STORE_ENV, str(store))
    package = tmp_path / "aliaspkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "macros.hy").write_text("(defmacro answer [] 11)\n")
    source = package / "user.hy"
    source.write_text("(require aliaspkg.macros [answer])\n(setv value (answer))\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    alias = "aliaspkg_user_by_path"
    try:
        assert importlib.import_module("aliaspkg.user").value == 11
        loader = importlib.machinery.SourceFileLoader(alias, str(source))
        spec = importlib.util.spec_from_file_location(alias, str(source), loader=loader)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[alias] = module
        loader.exec_module(module)
        assert module.value == 11
        entries = _stored_entries(store, alias, source, source.read_bytes())
        assert len(entries) == 1, f"path から別の名で読んだ code が共有の置き場に 1 つ入らない: {entries}"
        record = records.record_of(marshal.loads(Path(entries[0]).read_bytes()))
        assert record is not None
        assert [(used.module, used.name) for used in record.macros] == [("aliaspkg.macros", "answer")], (
            f"記録が展開の使った macro を名指さない: {record}"
        )
    finally:
        for loaded in (alias, "aliaspkg.user", "aliaspkg.macros", "aliaspkg"):
            sys.modules.pop(loaded, None)


def test_a_hy_module_read_from_a_current_pyc_does_not_look_up_the_expansion_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """作業木の .pyc が source に合う Hy の module の読み込みは compile しないので、展開の走る module(Hy の compile の口と同じく
    ``inspect.getmodule`` で file の名から引く)を引かない。引き方は読み込み済みの module 全部を歩くので、Hy の module 1 つごとに
    引くと、検の読み込み(約 800 の Hy の module・約 1500 の module)で CPU 約 0.6 秒の固定費になった(2026-10-11 の測り —
    test_emulated_screen.hy の固定費 3.5 → 4.1 秒)。"""
    import inspect

    import hy  # noqa: F401 — Hy の source を compile する口を載せる
    from doeff_hy_bytecode_guard import _code_store as code_store

    monkeypatch.setenv(code_store.STORE_ENV, str(tmp_path / "store"))
    monkeypatch.setattr(sys, "dont_write_bytecode", False)  # この suite は .pyc を書かない — ここだけ書いて 2 度目に読む
    package = tmp_path / "pycpkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "macros.hy").write_text("(defmacro answer [] 11)\n")
    source = package / "user.hy"
    source.write_text("(require pycpkg.macros [answer])\n(setv value (answer))\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    looked_up: list[str] = []
    getmodule = inspect.getmodule

    def counting_getmodule(target: object, _filename: str | None = None) -> object:
        if _filename == str(source):
            looked_up.append(_filename)
        return getmodule(target, _filename)

    try:
        assert importlib.import_module("pycpkg.user").value == 11
        assert looked_up == [], "数え始める前に引いた"  # 数えるのは 2 度目だけ
        assert list((package / "__pycache__").glob("user.*.pyc")), "1 度目の読み込みが .pyc を書かない"
        sys.modules.pop("pycpkg.user")
        monkeypatch.setattr(inspect, "getmodule", counting_getmodule)
        assert importlib.import_module("pycpkg.user").value == 11
        assert looked_up == [], f"使える .pyc を読むだけの読み込みが展開の module を {len(looked_up)} 回引いた"
    finally:
        for loaded in ("pycpkg.user", "pycpkg.macros", "pycpkg"):
            sys.modules.pop(loaded, None)


#: ``hy <script>``(runpy.run_path)と ``hy -m``(runpy.run_module)が呼ぶ入口 — hy の命令は引数を渡してこの関数を呼ぶだけ。
_RUN_BY_HY_COMMAND = "from hy.cmdline import cmdline_handler\ncmdline_handler({argv!r})\n"


def _write_scripts(root: Path) -> Path:
    """_write_counting_package の使う側に、同じ中身の script を 2 つ足す: ``runscript.hy``(root の直下 — path で撃つ)と
    ``pkg/tool.hy``(``hy -m pkg.tool`` で撃つ)。どちらも macro を 1 回展開して値を print する。返すのは path で撃つ script。"""
    _write_counting_package(root, 1)
    body = "(require pkg.macros [answer])\n(print (answer))\n"
    (root / "pkg" / "tool.hy").write_text(body)
    script = root / "runscript.hy"
    script.write_text(body)
    return script


def _hy_command(script: Path, by: str) -> str:
    """script を撃つ形(by = "path": ``hy <path>``・"module": ``hy -m pkg.tool``)の確かめの文。"""
    argv = ["hy", str(script)] if by == "path" else ["hy", "-m", "pkg.tool"]
    return _RUN_BY_HY_COMMAND.format(argv=argv)


def _script_pyc(root: Path, by: str) -> Path:
    """_write_scripts の script の .pyc(by = "path" は root の直下・"module" は pkg の下)。"""
    cache, stem = (root, "runscript") if by == "path" else (root / "pkg", "tool")
    [pyc] = (cache / "__pycache__").glob(f"{stem}.*.pyc")
    return pyc


@pytest.mark.parametrize("by", ["path", "module"])
def test_a_script_run_by_the_hy_command_is_not_expanded_again_by_the_next_run(tmp_path: Path, by: str) -> None:
    """``hy <script>`` と ``hy -m`` は import を通らず、runpy が読みの口の get_code を module を ``sys.modules`` に置く前に呼ぶ。
    Hy は仮の module で compile して直後に消すので、記録が付かず、その .pyc は次の読みで必ず古い物として compile し直され、
    保存先にも入らなかった(gate の script で 2 度目以降も CPU 約 13 秒・``hy <script>`` も ``hy -m`` も同じ)。
    module を置いた中で compile すれば、script も import した module と同じく記録つきの .pyc になり、2 度目は展開しない。
    macro が変わった時は今までどおり展開し直す(記録の照らしが効く)。"""
    script = _write_scripts(tmp_path)
    command = _hy_command(script, by)
    for _ in range(3):
        assert _probe_on_pypi_hy(tmp_path, "before-hy", command).strip() == "11"
        assert _expansions(tmp_path) == 1, "2 度目以降の撃ちが script の macro を展開し直した"
    record = records.record_of(marshal.loads(_script_pyc(tmp_path, by).read_bytes()[records.PYC_HEADER_BYTES :]))
    assert record is not None, "script の .pyc に記録が付かない"
    assert [(used.module, used.name) for used in record.macros] == [("pkg.macros", "answer")]
    _edit(tmp_path / "pkg" / "macros.hy", _counting_macros(2))
    assert _probe_on_pypi_hy(tmp_path, "before-hy", command).strip() == "12", "macro の変更の後も古い展開で動いた"
    assert _expansions(tmp_path) == 2


@pytest.mark.parametrize("by", ["path", "module"])
def test_a_script_run_in_a_new_tree_takes_the_stores_code_without_expanding(tmp_path: Path, by: str) -> None:
    """新しい作業木(同じ中身・別の絶対 path・.pyc なし)で同じ script を撃つと、別の木で撃った時に足された保存先の code を使い、
    展開し直さない(``hy <script>`` の保存先の鍵の名は file の名 ``runscript``・``hy -m pkg.tool`` は ``pkg.tool``)。"""
    store = tmp_path / "store"
    first, second = tmp_path / "first", tmp_path / "second"
    for root in (first, second):
        root.mkdir()
    scripts = {root: _write_scripts(root) for root in (first, second)}
    assert _probe_on_pypi_hy(first, "before-hy", _hy_command(scripts[first], by), store).strip() == "11"
    assert _expansions(first) == 1
    assert _probe_on_pypi_hy(second, "before-hy", _hy_command(scripts[second], by), store).strip() == "11"
    assert _expansions(second) == 0


def test_a_read_outside_import_gets_its_module_only_while_it_compiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """runpy が呼ぶ形(loader を作り、module を置かずに get_code を直に呼ぶ)で compile した Hy の code は、使った macro の記録を
    持ち、保存先に入る。置いた module は読みの後に残らない(``sys.modules`` を汚さない)。"""
    import hy  # noqa: F401 — Hy の source を compile する口を載せる
    from doeff_hy_bytecode_guard import _code_store as code_store

    store = tmp_path / "store"
    monkeypatch.setenv(code_store.STORE_ENV, str(store))
    package = tmp_path / "outsidepkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "macros.hy").write_text("(defmacro answer [] 11)\n")
    source = tmp_path / "outsidescript.hy"
    source.write_text("(require outsidepkg.macros [answer])\n(setv value (answer))\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    name = "outsidescript"
    try:
        loader = importlib.machinery.SourceFileLoader(name, str(source))
        code = loader.get_code(name)
        assert code is not None
        record = records.record_of(code)
        assert record is not None, "import の外で読んだ Hy の code に記録が付かない"
        assert [(used.module, used.name) for used in record.macros] == [("outsidepkg.macros", "answer")]
        assert name not in sys.modules, "読みの後に置いた module が残った"
        assert len(_stored_entries(store, name, source, source.read_bytes())) == 1
    finally:
        for loaded in (name, "outsidepkg.macros", "outsidepkg"):
            sys.modules.pop(loaded, None)


def test_a_read_outside_import_does_not_replace_another_files_module_of_the_same_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """script の名が、別の file から読み込み済みの module の名と同じ(``hy json.hy``)時は、その module を置き換えない —
    compile の間も、使い手の展開が import する同じ名の module は元のまま(記録は足さない — 今までどおり)。"""
    import hy  # noqa: F401 — Hy の source を compile する口を載せる
    from doeff_hy_bytecode_guard import _loader_hooks as loader_hooks

    source = tmp_path / "clashing.hy"
    source.write_text("(setv value 1)\n")
    other = types.ModuleType("clashing")
    other.__file__ = str(tmp_path / "elsewhere" / "clashing.py")
    monkeypatch.setitem(sys.modules, "clashing", other)
    loader = importlib.machinery.SourceFileLoader("clashing", str(source))
    with loader_hooks._expansion_recording(loader, str(source)):
        assert sys.modules["clashing"] is other
    assert sys.modules["clashing"] is other


#: Hy の ``(require 提供元 *)`` が使い手の表へ入れた名かを、表の名を読まずに確かめる値 — 使い手が同じ名の関数を持ち、呼ぶと
#: macro の展開なら macro の値、入らなかった名なら関数のこの答えになる(macro は同じ名の関数より先に引かれる)。
_PLAIN_FUNCTION = "plain function"


def _write_internal_names_package(package: Path) -> None:
    """Hy の表の名と export の名の表を、Hy 自身の振る舞いで確かめる package: 提供元 2 つ(export で公開を絞る provider・絞らない
    plain)と、全部を require する使い手 2 つ・reader macro を require する使い手 1 つ。"""
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "provider.hy").write_text(
        "(export :macros [answer _visible undefined])\n"
        "(defmacro answer [] 1)\n"
        "(defmacro hidden [] 2)\n"
        "(defmacro _visible [] 3)\n"
        "(defreader twice (setv x (.parse-one-form &reader)) `(* 2 ~x))\n"
    )
    (package / "plain.hy").write_text("(defmacro answer [] 1)\n(defmacro _private [] 2)\n")
    (package / "user_all.hy").write_text(
        f"(require {package.name}.provider *)\n"
        f'(defn hidden [] "{_PLAIN_FUNCTION}")\n'
        '(setv seen {"answer" (answer) "_visible" (_visible) "hidden" (hidden)})\n'
    )
    (package / "user_plain.hy").write_text(
        f"(require {package.name}.plain *)\n"
        f'(defn _private [] "{_PLAIN_FUNCTION}")\n'
        '(setv seen {"answer" (answer) "_private" (_private)})\n'
    )
    (package / "user_reader.hy").write_text(
        f"(require {package.name}.provider :readers [twice])\n(setv value #twice 5)\n"
    )


def test_records_reads_the_macro_tables_and_the_exported_names_the_way_hy_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hy が module に置く macro の表(普通の macro・reader macro)と、``(require 提供元 *)`` が入れる名の読みは、定義元
    (``records.macro_tables``・``records.exported_macro_names``)が実物の Hy の module で Hy 自身の振る舞いと一致する —
    表の名(``MACRO_TABLE``・``READER_TABLE``)と export の名の表(``EXPORT_NAMES``)の字は records の 1 か所なので、Hy が名を
    替えるとここが赤になる(result_cache・macro_use・包みの検が読む名の定義元)。入る名の側は表の名を
    読まず、使い手が同じ名の関数を持つ形(macro が先に引かれる)で測る。"""
    import hy  # noqa: F401 — Hy の source を compile する口を載せる
    from doeff_hy_bytecode_guard import _code_store as code_store

    monkeypatch.setenv(code_store.STORE_ENV, str(tmp_path / "store"))
    name = "internalnamespkg"
    _write_internal_names_package(tmp_path / name)
    monkeypatch.syspath_prepend(str(tmp_path))
    modules = ("provider", "plain", "user_all", "user_plain", "user_reader")
    try:
        loaded = {each: importlib.import_module(f"{name}.{each}") for each in modules}

        def tables(module: object) -> list[list[str]]:
            return [sorted(table) for table in records.macro_tables(vars(module))]

        def macros_entered(user: object) -> frozenset[str]:
            """使い手の ``seen`` のうち、macro の展開になった名(関数の答えでない値)。"""
            return frozenset(key for key, value in vars(user)["seen"].items() if value != _PLAIN_FUNCTION)

        assert macros_entered(loaded["user_all"]) == {"answer", "_visible"}, "export が絞った名だけ入る"
        assert records.exported_macro_names(vars(loaded["provider"])) == macros_entered(loaded["user_all"])
        assert macros_entered(loaded["user_plain"]) == {"answer"}, "export が無ければ _ で始まらない名が入る"
        assert records.exported_macro_names(vars(loaded["plain"])) == macros_entered(loaded["user_plain"])
        assert tables(loaded["provider"]) == [["_visible", "answer", "hidden"], ["twice"]]
        assert tables(loaded["user_all"]) == [["_visible", "answer"], []]
        assert [each for table in tables(loaded["user_reader"]) for each in table] == ["twice"]
        assert vars(loaded["user_reader"])["value"] == 10
    finally:
        for each in (*(f"{name}.{each}" for each in modules), name):
            sys.modules.pop(each, None)


#: Hy が一時の名を振る 3 つの形を 1 つずつ含む source — match を値に使う(``_hy_anon_<数え>``)・let(``_hy_let_<元の名>_<数え>``)・
#: except の束縛(``_hy_exc_<元の名>_<数え>``)。
_TEMPORARIES_SOURCE = """\
(defn f [a]
  (setv x (match a 1 "one" _ "other"))
  (let [first 1 second 2 x1 3] (+ first second x1))
  (try (g) (except [e Exception] e))
  (try (g) (except [e2 Exception] e2))
  x)
"""
#: 同じ source の前に let を 1 つ足した物 — 後ろの一時の名の番号がずれる(定義を足すだけで起きる)。
_TEMPORARIES_SHIFTED = "(defn shifted [] (let [first 1] first))\n" + _TEMPORARIES_SOURCE


def _hy_temporaries(source: str) -> frozenset[str]:
    """source を Hy で compile した木の名のうち、source の綴りに無い物 — Hy が振った一時の名(名の先頭は読まない: Hy が先頭を
    替えても、替わった名を集める)。"""
    import ast
    import re
    import types

    import hy
    import hy.compiler

    module = types.ModuleType("internal_names_probe")
    module.__file__ = "internal_names_probe.hy"
    tree = hy.compiler.hy_compile(
        hy.read_many(source, filename=module.__file__), module, filename=module.__file__, source=source
    )
    written = frozenset(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", source))
    named: set[str] = set()
    for node in ast.walk(tree):
        match node:
            case ast.Name(id=name) | ast.arg(arg=name) | ast.FunctionDef(name=name) | ast.ExceptHandler(name=str(name)):
                named.add(name)
    return frozenset(named) - written


def test_the_temporaries_hy_names_have_the_shape_records_folds() -> None:
    """Hy が一時の名(match を値に使った ``_hy_anon_<数え>``・let の ``_hy_let_<元の名>_<数え>``・except の
    ``_hy_exc_<元の名>_<数え>``)に振る形は、定義元 ``records.ANON_VAR_NAME`` が読む形のまま — 数えを畳んだ名は、定義を足して番号が
    ずれても同じ(doeff-hy-check の基点が、同じ赤を番号の違いで新しい赤と数えない)。Hy が名の形を替えると、畳めない名が出て
    ここが赤になる(形は records の 1 か所。doeff_hy._static_baseline・static_stub は字を持たない)。"""
    from doeff_hy._static_baseline import comparable_message
    from doeff_hy._static_stub import hidden_name

    plain = _hy_temporaries(_TEMPORARIES_SOURCE)
    shifted = _hy_temporaries(_TEMPORARIES_SHIFTED)
    assert plain != shifted, "番号がずれていない — この検が番号のずれを測れていない"
    folded = {name: records.fold_anon_var_numbers(name, "#") for name in plain | shifted}
    assert {folded[name] for name in plain} == {
        "_hy_anon_#",
        "_hy_exc_e_#",
        "_hy_let_first_#",
        "_hy_let_second_#",
        "_hy_let_x1_#",
        "_hy_exc_e2_#",
    }, f"Hy の振った一時の名が、畳める形でない(元の名が数字で終わる名も): {sorted(plain)}"
    assert {folded[name] for name in shifted} == {folded[name] for name in plain}
    assert {comparable_message(f"x is {name}") for name in plain} == {
        comparable_message(f"x is {name}") for name in shifted
    } == {f"x is {each}" for each in set(folded.values())}, "基点の鍵が、一時の名の番号に依っている"
    assert all(hidden_name(name) for name in plain | shifted), "型の面が、Hy の振った一時の名を隠さない"
