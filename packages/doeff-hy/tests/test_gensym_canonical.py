"""Hy の gensym の名を module の中の順の通し番号にする正準化(doeff_hy_bytecode_guard.records.canonical_gensyms・agora-redesign
#3667)の反例。

Hy の ``hy.gensym`` の数えは process に 1 つで、gensym を呼ぶたびに進む。そのため同じ source でも、先に別の module を compile した
か・require 先の .pyc が在ったか・別の thread が同時に compile していたかで、局所変数の名(``_hy_gensym_bound_1890`` と ``_3015``)が
変わり、.pyc・共有の code の置き場・詰めた Program の指紋(doeff-cluster の program_codec)が揺れていた。compile の口(包みの
source_to_code)が compile の済んだ code の gensym の名を振り直すので、どの道の compile も同じ code になる。

見本の module は tmp_path に書く。別の process の検は .pyc の置き場(PYTHONPYCACHEPREFIX)をこの file の中で共有し(最初の子だけが
doeff を compile する)、共有の code の置き場は切る(DOEFF_HY_CODE_STORE=off — 置き場から引くと compile が起きない)。
"""

from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import json
import re
import subprocess
import sys
import threading
import types
from collections.abc import Iterator
from pathlib import Path
from types import CodeType

import pytest
from doeff_hy_bytecode_guard import code_store, records, source_to_code_as_import

#: 直す前の記録の印(gensym の名を正準化していない code の記録)。
PREVIOUS_RECORD_TAG = "doeff-hy/macro-dependencies/1"

#: 見本の package の名(子の process と、この process の import の両方で使う)。
PACKAGE = "gsprobe"

#: gensym を使う doeff-hy の macro を全部通る見本(<- の束ね・absent-as の let・on-raise の受け・defrecord の :check・defk の契約の
#: check・validate の check・defhandler の session val — lazy の一時の名)。job は validate を通らずに走れる(validate は Traverse の handler が要る)。
EVERYTHING = """\
(require doeff-hy.macros [defk <- absent-as on-raise validate check])
(require doeff-hy.handle [defhandler])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_core_effects [Ask])

(defclass [(dataclass :frozen True)] Fetch [EffectBase]
  #^ str source)

(defclass [(dataclass :frozen True)] Conflict []
  "版の負け(on-raise の受けの型)。"
  #^ str detail)

(defrecord Positive
  "正の数"
  {:tags {:context "gensym-probe" :role "type"}
   :check [(> value 0)]}
  (#^ int value))

(defk plus-one [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "gensym-probe" :role "judgment"}}
  "1 を足す(束ねの相手)。"
  (+ n 1))

(defk job [n]
  {:pre [(: n int) (check > n 0 :reason "正")] :post [(: % int)] :tags {:context "gensym-probe" :role "entry"}}
  "gensym を使う macro を通る job。"
  (<- a (plus-one n))
  (<- b (absent-as 0 (plus-one a)))
  (<- c (on-raise (plus-one b) (Conflict d) 0))
  (+ a b c (. (Positive :value 1) value)))

(defk validated [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "gensym-probe" :role "entry"}}
  "validate の check を通る(compile だけを見る)。"
  (! (validate (check = n n :reason "同じ")))
  n)

(defhandler fetch-handler
  (session val client (+ "client:" (! (Ask "endpoint"))))
  (Fetch [source]
    (resume (+ client ":" source))))
"""

#: 先に compile して数えを進める別の module(gensym と session val の一時の名の両方)。
OTHER = """\
(require doeff-hy.macros [defk <-])
(require doeff-hy.handle [defhandler])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_core_effects [Ask])

(defclass [(dataclass :frozen True)] Other [EffectBase]
  #^ str source)

(defk step [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "gensym-probe" :role "judgment"}}
  "1 を足す。"
  (+ n 1))

(defk other-job [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "gensym-probe" :role "entry"}}
  "束ね 3 つ。"
  (<- a (step n))
  (<- b (step a))
  (<- c (step b))
  c)

(defhandler other-handler
  (session val connection (+ "connection:" (! (Ask "endpoint"))))
  (Other [source]
    (resume (+ connection ":" source))))
"""

#: require される macro の提供元 — 自分の compile でも gensym を使う(require する側の compile の途中で compile される)。
MYMACROS = """\
(require doeff-hy.macros [defk <-])

(defk helper-step [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "gensym-probe" :role "judgment"}}
  "1 を足す。"
  (+ n 1))

(defk helper [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "gensym-probe" :role "judgment"}}
  "束ね 1 つ。"
  (<- a (helper-step n))
  a)

(defmacro twice [x]
  (setv g (hy.gensym "twice"))
  `(do (setv ~g ~x) (+ ~g ~g)))
"""

#: compile の途中で mymacros を require する module。
NESTED = f"""\
(require {PACKAGE}.mymacros [twice])
(require doeff-hy.macros [defk <-])
(import {PACKAGE}.mymacros [helper])

(defk job [n]
  {{:pre [(: n int)] :post [(: % int)] :tags {{:context "gensym-probe" :role "entry"}}}}
  "require した macro の gensym を使う job。"
  (<- a (helper n))
  (twice a))
"""

#: 子の process: 見本の根を sys.path に足し、名指した module を順に import して、的の module の code(import の時に compile か
#: .pyc から読んだ物)の生成名と、job を走らせた答えと、import の前に .pyc が在った見本の module の名を返す。
_CHILD = f"""\
import importlib, importlib.machinery, importlib.util, json, os, re, sys
root, first, target = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, root)
import doeff_hy
from doeff import run

package = os.path.join(root, "{PACKAGE}")
had_pyc = sorted(
    "{PACKAGE}." + name[: -len(".hy")]
    for name in os.listdir(package)
    if name.endswith(".hy") and os.path.exists(importlib.util.cache_from_source(os.path.join(package, name)))
)
codes = {{}}
previous_get_code = importlib.machinery.SourceFileLoader.get_code


def get_code(self, fullname):
    code = previous_get_code(self, fullname)
    codes[fullname] = code
    return code


importlib.machinery.SourceFileLoader.get_code = get_code
for name in filter(None, first.split(",")):
    importlib.import_module(name)
module = importlib.import_module(target)


def texts(code):
    yield from (*code.co_varnames, *code.co_cellvars, *code.co_freevars, *code.co_names)
    for constant in code.co_consts:
        if isinstance(constant, type(code)):
            yield from texts(constant)
        elif isinstance(constant, str):
            yield constant


generated = sorted({{
    text for text in texts(codes[target])
    if "_hy_gensym_" in text or re.fullmatch(r"_lazy_.+_(cached|val)_[0-9]+", text)
}})
value = run(module.job(1)) if hasattr(module, "job") else None
print(json.dumps({{"names": generated, "value": value, "had_pyc": had_pyc}}))
"""

#: Hy の gensym の名(``_hy_gensym_<base>_<数え>``)と、Hy が改めた束縛の名に埋まった物(``_hy_<種>_<gensym の名>_<数>``)。
_GENSYM = re.compile(r"(_hy_gensym_.*_)([0-9]+)")
_RENAMED = re.compile(r"_hy_[a-z]+_(_hy_gensym_.*)_[0-9]+")


@pytest.fixture(scope="module")
def pycache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """この file の子の process が共有する .pyc の置き場(最初の子だけが doeff と Hy を compile する)。"""
    return tmp_path_factory.mktemp("gensym-pycache")


def _probe(root: Path) -> Path:
    """root に見本の package を置き、root を返す(同じ中身の package を別の root に置けば、的の .pyc は共有されない)。"""
    package = root / PACKAGE
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    for name, source in (
        ("everything", EVERYTHING),
        ("other", OTHER),
        ("mymacros", MYMACROS),
        ("nested", NESTED),
    ):
        (package / f"{name}.hy").write_text(source)
    return root


def _binds_module(binds: int) -> str:
    """束ねを binds 個並べた defk 1 つの module の source(同じ binds なら同じ source)— 並べて compile する検の材料。"""
    lines = [f"  (<- v{index} (step {index}))" for index in range(binds)]
    return "\n".join(
        [
            "(require doeff-hy.macros [defk <-])",
            "(defk step [n]",
            '  {:pre [(: n int)] :post [(: % int)] :tags {:context "gensym-probe" :role "judgment"}}',
            '  "1 を足す。"',
            "  (+ n 1))",
            "(defk many [n]",
            '  {:pre [(: n int)] :post [(: % int)] :tags {:context "gensym-probe" :role "entry"}}',
            '  "束ねを並べる。"',
            *lines,
            "  n)",
            "",
        ]
    )


def _child(root: Path, first: list[str], target: str, pycache: Path) -> dict[str, object]:
    """この venv の python で別の process を走らせ、first を順に import してから target を import した結果(生成名・job の答え・
    compile した module)を返す。.pyc は書く(require 先の .pyc の有無を作るため)— 置き場は pycache。"""
    # 子はこの process の環境を継ぐ — この suite の固定の PYTHONDONTWRITEBYTECODE=1 だけ外し(`env -u`)、置き場の 2 つを足す。
    done = subprocess.run(
        [
            "env",
            "-u",
            "PYTHONDONTWRITEBYTECODE",
            f"PYTHONPYCACHEPREFIX={pycache}",
            f"{code_store.STORE_ENV}=off",
            sys.executable,
            "-c",
            _CHILD,
            str(root),
            ",".join(first),
            target,
        ],
        capture_output=True,
        text=True,
        cwd=root,
        timeout=50,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    answer = json.loads(done.stdout.splitlines()[-1])
    assert isinstance(answer, dict)
    return answer


def _texts(code: CodeType) -> Iterator[str]:
    """code の木の名の表と文字列の定数(生成名が現れる所)。"""
    yield from (*code.co_varnames, *code.co_cellvars, *code.co_freevars, *code.co_names)
    for constant in code.co_consts:
        if isinstance(constant, CodeType):
            yield from _texts(constant)
        elif isinstance(constant, str):
            yield constant


def _gensym_names(code: CodeType) -> list[str]:
    """code の木の gensym の名(Hy が改めた束縛の名に埋まった物は、埋まった gensym の名)を並べる。"""
    found = set()
    for text in _texts(code):
        renamed = _RENAMED.fullmatch(text)
        name = renamed.group(1) if renamed is not None else text
        if _GENSYM.fullmatch(name):
            found.add(name)
    return sorted(found)


def _numbers(code: CodeType) -> list[int]:
    """code の木の gensym の名の番号(小さい順)。"""
    return sorted(int(match.group(2)) for name in _gensym_names(code) if (match := _GENSYM.fullmatch(name)))


def _compile(root: Path, module: str) -> CodeType:
    """見本の module を、前もって bytecode を作る道具と同じ口(module を置いた中の compile)で compile する。"""
    path = root / PACKAGE / f"{module}.hy"
    loader = importlib.machinery.SourceFileLoader(f"{PACKAGE}.{module}", str(path))
    return source_to_code_as_import(loader, path.read_bytes(), str(path))


@pytest.fixture
def probe_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """この process で import できる見本の package の根(終わったら見本の module を sys.modules から外す)。"""
    root = _probe(tmp_path / "probe")
    monkeypatch.syspath_prepend(str(root))
    yield root
    for name in [name for name in sys.modules if name == PACKAGE or name.startswith(f"{PACKAGE}.")]:
        del sys.modules[name]


def test_a_module_gets_the_same_names_whether_another_module_was_compiled_first(
    tmp_path: Path, pycache: Path
) -> None:
    """失敗ケース (a)(d): 同じ module を「別の module を先に compile した process」と「単独の process」で compile して、生成名
    (absent-as の let に埋まった名と defhandler の session val の一時の名を含む)が同じ。直す前は数えの続きの番号になり、名が違った。"""
    after_other = _child(
        _probe(tmp_path / "after"), [f"{PACKAGE}.other"], f"{PACKAGE}.everything", pycache
    )
    alone = _child(_probe(tmp_path / "alone"), [], f"{PACKAGE}.everything", pycache)
    assert after_other["had_pyc"] == alone["had_pyc"] == [], "見本の .pyc が先に在った(今の compile を測れていない)"
    assert after_other["names"] == alone["names"]
    names = alone["names"]
    assert isinstance(names, list)
    assert any(name.startswith("_hy_let__hy_gensym_absent_token_") for name in names), names
    assert any(name.startswith("_hy_gensym_lazy_client_") for name in names), names
    assert alone["value"] == after_other["value"] == 10


def test_a_module_gets_the_same_names_whether_the_required_macro_module_has_bytecode_or_not(
    tmp_path: Path, pycache: Path
) -> None:
    """失敗ケース (b): require 先(mymacros)の .pyc が在る process と無い process で、require する側(nested)の生成名が同じ。
    直す前は、require 先の compile が require する側の compile の途中で数えを進め、名がずれた。"""
    warm_root = _probe(tmp_path / "warm")
    _child(warm_root, [], f"{PACKAGE}.mymacros", pycache)
    warm = _child(warm_root, [], f"{PACKAGE}.nested", pycache)
    cold = _child(_probe(tmp_path / "cold"), [], f"{PACKAGE}.nested", pycache)
    assert warm["had_pyc"] == [f"{PACKAGE}.mymacros"], warm["had_pyc"]
    assert cold["had_pyc"] == [], cold["had_pyc"]
    assert warm["names"] == cold["names"]
    assert warm["value"] == cold["value"] == 4


def test_compiles_on_two_threads_at_once_keep_every_name_apart_and_the_same(probe_root: Path) -> None:
    """失敗ケース (c): 束ね BINDS 個の module 2 つを、2 つの thread で同時に compile する(ROUNDS 回)。片方の thread は先に
    回ごとに大きさの違う前置きの module を compile してから始めるので、2 つの compile の重なり方が回ごとにずれる。module の中の
    束ねの名の数が束ねの数と同じ(名がぶつからない)で、名が毎回同じ。直す前は、2 つの compile の gensym が 1 つの数えを取り
    合い、名が回ごとに変わった。"""
    rounds, binds = 4, 80
    for turn in range(rounds):
        for side in ("a", "b"):
            (probe_root / PACKAGE / f"race_{turn}_{side}.hy").write_text(_binds_module(binds))
        (probe_root / PACKAGE / f"lead_{turn}.hy").write_text(_binds_module(15 * turn))
    modules = [f"race_{turn}_{side}" for turn in range(rounds) for side in ("a", "b")]
    results: dict[str, list[str]] = {}

    def compile_on_thread(modules_in_order: list[str], start: threading.Barrier) -> None:
        """start で揃ってから modules_in_order を順に compile し、最後の module の生成名を残す(2 つの compile を重ねるため)。"""
        start.wait()
        for module in modules_in_order:
            results[module] = _gensym_names(_compile(probe_root, module))

    for turn in range(rounds):
        start = threading.Barrier(2)
        threads = [
            threading.Thread(target=compile_on_thread, args=([f"race_{turn}_a"], start)),
            threading.Thread(target=compile_on_thread, args=([f"lead_{turn}", f"race_{turn}_b"], start)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    assert set(modules) <= set(results)
    first = results[modules[0]]
    for module in modules:
        bound = [name for name in results[module] if name.startswith("_hy_gensym_bound_")]
        assert len(bound) == binds, (module, len(bound))
        assert results[module] == first, module


def test_a_pyc_whose_record_has_the_previous_tag_is_compiled_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """失敗ケース (e): 直す前の記録の印の .pyc(gensym の名を正準化していない code)は、source と時刻が合っていても使わず compile
    し直す。直す前の印のままだと、古い .pyc の中身がそのまま使われ続ける。"""
    import hy

    monkeypatch.setenv(code_store.STORE_ENV, "off")
    # この suite は PYTHONDONTWRITEBYTECODE=1 で走る — .pyc の読みと書きの道をこの検の中だけ開ける(置き場は tmp_path)。
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    monkeypatch.setattr(sys, "pycache_prefix", str(tmp_path / "pyc"))
    package = tmp_path / "oldtagpkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    source = package / "mod.hy"
    source.write_text('(setv value "new")\n')
    stale = compile("value = 'old'\n", str(source), "exec")
    stale = stale.replace(co_consts=(*stale.co_consts, (PREVIOUS_RECORD_TAG, hy.__version__, ())))
    status = source.stat()
    pyc = Path(importlib.util.cache_from_source(str(source)))
    pyc.parent.mkdir(parents=True)
    pyc.write_bytes(
        records.pyc_bytes(
            stale,
            source=source.read_bytes(),
            mtime=int(status.st_mtime),
            size=status.st_size,
            previous_header=None,
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        assert importlib.import_module("oldtagpkg.mod").value == "new"
    finally:
        for name in ("oldtagpkg.mod", "oldtagpkg"):
            sys.modules.pop(name, None)


def test_the_image_build_compile_and_the_prebuild_compile_number_gensyms_from_one(
    probe_root: Path,
) -> None:
    """image の組み立て(agora-controllers の deploy/bytecode.py — ``SourceFileLoader(...).source_to_code`` を直に呼ぶ)と、前もって
    bytecode を作る道具(source_to_code_as_import)の compile も同じ口を通り、gensym の番号が 1 から隙間なく並ぶ(この process の
    数えはもう大きい)。直す前は、どちらも process の数えの続きの番号だった。"""
    path = probe_root / PACKAGE / "everything.hy"
    direct = importlib.machinery.SourceFileLoader(f"{PACKAGE}.everything", str(path)).source_to_code(
        path.read_bytes(), str(path)
    )
    prebuilt = _compile(probe_root, "everything")
    assert _numbers(direct) == list(range(1, len(_numbers(direct)) + 1)), _gensym_names(direct)
    assert _gensym_names(direct) == _gensym_names(prebuilt)


def test_the_canonical_module_still_runs(probe_root: Path) -> None:
    """正準化した code の module は今までどおり動く(名の表と定数を 1 対 1 で替えただけ — 束ね・absent-as・on-raise・契約の check・
    defrecord の :check を通る job の答え)。"""
    from doeff import run

    module = importlib.import_module(f"{PACKAGE}.everything")
    assert run(module.job(1)) == 10


def test_hy_gensym_names_and_hy_renamed_bindings_have_the_shapes_the_canonicalisation_reads() -> None:
    """Hy の gensym の名の形(``_hy_gensym_<mangle した base>_<数え>``)と、let が改めた束縛の名の形(``_hy_let_<元の名>_<数>``)が、
    正準化の読む形のまま。Hy を上げて形が変わると、正準化が名を読めずに黙って揺れが戻るので、ここで落とす。"""
    import hy
    from hy.compiler import hy_compile
    from hy.models import Expression, Integer, List, Symbol

    for base, mangled in (("bound", "bound"), ("el-begin", "el_begin"), ("", "")):
        name = str(hy.gensym(base))
        match = records.GENSYM_NAME.fullmatch(name)
        assert match is not None and match.group(1) == f"_hy_gensym_{mangled}_", name
    token = hy.gensym("token")
    tree = Expression(
        [
            Symbol("defn"),
            Symbol("f"),
            List([]),
            Expression([Symbol("let"), List([token, Integer(1)]), token]),
        ]
    )
    compiled = hy_compile(tree, types.ModuleType("gsshape"))
    assert isinstance(compiled, ast.Module)  # get_expr を渡さない時の答えは module だけ
    renamed = [
        node.id
        for node in ast.walk(compiled)
        if isinstance(node, ast.Name) and node.id.startswith("_hy_let_")
    ]
    assert renamed, ast.dump(compiled)
    for name in renamed:
        match = records.RENAMED_BINDING.fullmatch(name)
        assert match is not None and match.group(2) == str(token), name


def test_no_gensym_name_is_left_where_the_canonicalisation_cannot_read_it(probe_root: Path) -> None:
    """gensym を使う doeff-hy の macro を全部通る module の compile の後、gensym の名を含むのに正準化が読めない名・文字列の定数が
    1 つも無い(文の中に埋まった名などが現れると、その番号は compile の順で変わる)。読めない物を見分けられることも確かめる。"""
    assert records.unread_gensym_texts(_compile(probe_root, "everything")) == ()
    leftover = compile("message = 'bad _hy_gensym_x_5 here'\n", "<leftover>", "exec")
    assert records.unread_gensym_texts(leftover) == ("bad _hy_gensym_x_5 here",)
