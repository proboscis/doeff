"""item を作る macro が展開の時に書く pytest の item の記録(doeff_hy/pytest_items.py・agora-redesign #1222 / #1211)。

記録は Hy の importer が pyc の隣に書くので、pyc を書く新しい interpreter で test module を import してから、
別の新しい interpreter で import せずに読む(この pytest の process は bytecode を書かない設定で走る — conftest.py)。
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

from doeff_hy.pytest_items import (
    Dynamic,
    FunctionItem,
    LiteralValue,
    Mark,
    ModuleMarks,
    OpaqueValue,
    Parametrize,
    RecordedModule,
    SkipIf,
    read_module,
)

CORPUS = """
(require doeff-hy.macros [deftest val])
(import pytest)
(val pytestmark pytest.mark.real-world)
(val RIGS [1 2])
(deftest test-plain (assert True))
(deftest test-marked {:marks ["slow" "e2e"]} (assert True))
(deftest test-params [x y] {:params {"x" ["a" 1 2.5 True None] "y" [[1 2] :k]}} (assert True))
(deftest test-interp {:interpreters ["a" "b"] :skip-if (= 1 2) :skip-reason "never"} (assert True))
(deftest test-dynamic [open-rig] {:params {"open_rig" RIGS}} (assert True))
"""


def _python(root: Path, code: str) -> str:
    """pyc を tmp の下の置き場へ書く新しい interpreter で ``code`` を走らせ、標準出力を返す(記録は pyc と一緒にだけ書かれる)。"""
    # pyc の置き場は tmp の下(checkout の中に pyc を書かない — conftest.py の _bytecode_settings_pinned)。
    # 書く process と読む process が同じ置き場を見るので、記録も同じ所から読まれる。
    env = {
        **os.environ,
        "PYTHONPATH": str(root),
        "PYTHONDONTWRITEBYTECODE": "",
        "PYTHONPYCACHEPREFIX": str(root / ".pyc"),
    }
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _recorded(root: Path, module: str) -> RecordedModule | None:
    """記録を import せずに読む(読んだ process がその module を import していないことも確かめる)。"""
    out = _python(
        root,
        f"""
        import pickle, sys
        import doeff_hy
        from hy.importer import read_valid_records
        from doeff_hy.pytest_items import read_module
        records = read_valid_records({str(root / (module + ".hy"))!r})
        assert {module!r} not in sys.modules
        sys.stdout.write(pickle.dumps(None if records is None else read_module(records)).hex())
        """,
    )
    import pickle

    return pickle.loads(bytes.fromhex(out))


def _import(root: Path, module: str) -> None:
    _python(root, f"import doeff_hy, {module}")


def test_deftest_records_each_function_in_pytest_terms(tmp_path: Path) -> None:
    """deftest の鍵(:marks・:params・:interpreters・:skip-if)は、pytest の言葉に直した形で記録される。"""
    (tmp_path / "test_corpus.hy").write_text(CORPUS)
    assert _recorded(tmp_path, "test_corpus") is None
    _import(tmp_path, "test_corpus")
    recorded = _recorded(tmp_path, "test_corpus")
    assert recorded is not None
    assert recorded.records == (
        ModuleMarks(("real_world",)),
        FunctionItem("test_plain", ("doeff_interpreter",), ()),
        FunctionItem("test_marked", ("doeff_interpreter",), (Mark("slow"), Mark("e2e"))),
        FunctionItem(
            "test_params",
            ("doeff_interpreter", "x", "y"),
            (
                Parametrize(
                    "x",
                    (
                        LiteralValue("a"),
                        LiteralValue(1),
                        LiteralValue(2.5),
                        LiteralValue(True),
                        LiteralValue(None),
                    ),
                ),
                Parametrize("y", (OpaqueValue(), OpaqueValue())),
            ),
        ),
        FunctionItem(
            "test_interp",
            ("doeff_interpreter",),
            (Parametrize("doeff_interpreter_name", (LiteralValue("a"), LiteralValue("b"))), SkipIf()),
        ),
        Dynamic("test_dynamic", "values 'RIGS"),
    )
    assert {"pytestmark", "test_plain", "test_dynamic", "RIGS"} <= recorded.bound_names


def test_records_follow_the_source(tmp_path: Path) -> None:
    """source を書き換えると記録は無効になり、次の import が新しい記録を書く。"""
    source = tmp_path / "test_corpus.hy"
    source.write_text(CORPUS)
    _import(tmp_path, "test_corpus")
    source.write_text("(require doeff-hy.macros [deftest])\n(deftest test-only (assert True))\n")
    later = source.stat().st_mtime_ns + 10**10
    os.utime(source, ns=(later, later))
    assert _recorded(tmp_path, "test_corpus") is None
    _import(tmp_path, "test_corpus")
    recorded = _recorded(tmp_path, "test_corpus")
    assert recorded is not None
    assert recorded.records == (FunctionItem("test_only", ("doeff_interpreter",), ()),)


def test_module_marks_that_are_not_literal_are_dynamic(tmp_path: Path) -> None:
    """``pytest.mark.<名>`` の形でない pytestmark は、展開の時に決まらないので Dynamic になる。"""
    (tmp_path / "test_marks.hy").write_text(
        "(require doeff-hy.macros [val])\n(import pytest)\n"
        "(val pytestmark (pytest.mark.skipif True :reason \"r\"))\n"
    )
    _import(tmp_path, "test_marks")
    recorded = _recorded(tmp_path, "test_marks")
    assert recorded is not None
    assert [type(r) for r in recorded.records] == [Dynamic]


def test_read_module_of_records_without_items() -> None:
    """item の記録の無い module(記録は Hy の束縛の名だけ)は、item 0 の記録として読める。"""
    assert read_module({"hy.bound-names": ["x"], "hy.decorators": {"f": ["pytest.fixture"]}}) == RecordedModule(
        (), frozenset({"x"}), {"f": ("pytest.fixture",)}, frozenset()
    )
