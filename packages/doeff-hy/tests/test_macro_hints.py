"""macro を require し忘れた Hy の module と、macro を import で入れた module に、書き足す 1 行を言う検(取り組み doeff-publish・card ki-909e814cc835)。

初見の人が README の Hy の例から ``(require doeff-hy.macros [do! <-])`` を抜くと、Hy は ``do!`` を関数の呼び出しとして compile し、
実行の時に ``NameError: name 'hyx_doXexclamation_markX' is not defined`` になる(Hy の仕様 — Hy には手を入れない)。macro を
``(import doeff-hy.handle [defhandler])`` で入れると ``ImportError: cannot import name 'defhandler' …`` になる。どちらも文は
Python が出し、直し方を言わない。doeff-hy は macro の名と提供元の module を知るので、Hy の module を読む口
(doeff_hy_bytecode_guard の get_code — ``hy file.hy``・``hy -m``・import・pytest の全部が通る)で、書き足す 1 行を警告に出す。

子の process で Hy の module を走らせ、標準エラーを読む(README の例を走らせる検と同じく、新しい interpreter で ``-m hy`` を使う)。
共有の code の置き場は切り(DOEFF_HY_CODE_STORE=off)、bytecode は検ごとの置き場に書く(2 回目が .pyc 経由の読みになる)。

「Python から .hy を入れる時の ``import hy``」(card の 18)は doeff の code を足さない — 理由は card の発言(責任の一覧と要否)と
docs/01-getting-started.md の節 Using Hy。例は README と 01 章の囲みを走らせる既存の検が持つ。
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from doeff_hy_bytecode_guard import records

#: README の Hy の例(``(require doeff-hy.macros [do! <-])`` の行を持つ正しい形)。
CORRECT = """\
(require doeff-hy.handle [defhandler])
(require doeff-hy.macros [do! <-])
(import doeff [Ask run])

(defhandler ask-env
  (Ask [key]
    (resume (get {"greeting" "hello"} key))))

(setv greet
  (do!
    (<- greeting (Ask "greeting"))
    greeting))

(print (run (ask-env greet)))
"""
#: 同じ例から ``do!`` と ``<-`` の require の行を抜いた形(card の 17・cases/h01_hy_forgot_require_macros)。
FORGOT_REQUIRE = CORRECT.replace("(require doeff-hy.macros [do! <-])\n", "")
#: ``defhandler`` を require でなく import で入れた形(cases/h02_hy_import_instead_of_require)。
IMPORT_INSTEAD = CORRECT.replace(
    "(require doeff-hy.handle [defhandler])", "(import doeff-hy.handle [defhandler])"
)
#: 子の process の制限時間の、負荷で伸ばす前の値(秒)。1 本は 約 1〜2 秒(doeff と hy の import)。
CHILD_DEADLINE_SECONDS = 50


def _run(
    directory: Path, deadline_scale_factor: float, *arguments: str
) -> subprocess.CompletedProcess[str]:
    """directory で新しい interpreter を走らせる(bytecode は directory の pyc に書く — 2 回目は .pyc を読む)。"""
    environment = {**os.environ, "DOEFF_HY_CODE_STORE": "off"}
    return subprocess.run(
        [sys.executable, "-E", "-s", "-X", f"pycache_prefix={directory / 'pyc'}", *arguments],
        cwd=directory,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
    )


def _hy_script(directory: Path, text: str, deadline_scale_factor: float) -> subprocess.CompletedProcess[str]:
    """text を main.hy に書き、README の例と同じ ``python -m hy main.hy`` で走らせる。"""
    (directory / "main.hy").write_text(text, encoding="utf-8")
    return _run(directory, deadline_scale_factor, "-m", "hy", "main.hy")


def test_a_forgotten_require_of_do_and_arrow_says_the_line_to_add(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    result = _hy_script(tmp_path, FORGOT_REQUIRE, deadline_scale_factor)
    # 失敗そのものは変えない(NameError のまま) — 警告が先に、直す 1 行を言う。
    assert result.returncode != 0
    assert "NameError: name 'hyx_doXexclamation_markX' is not defined" in result.stderr
    assert "(require doeff-hy.macros [do! <-])" in result.stderr
    assert result.stderr.index("(require doeff-hy.macros [do! <-])") < result.stderr.index("Traceback")
    # 警告は main.hy の使った行を指す(do! の行)。
    assert "main.hy:10:" in result.stderr


def test_the_second_run_reads_a_pyc_and_still_says_the_line(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    first = _hy_script(tmp_path, FORGOT_REQUIRE, deadline_scale_factor)
    assert list((tmp_path / "pyc").rglob("*.pyc")), "1 回目が .pyc を書いていない — 2 回目が .pyc 経由にならない"
    second = _hy_script(tmp_path, FORGOT_REQUIRE, deadline_scale_factor)
    assert "(require doeff-hy.macros [do! <-])" in first.stderr
    assert "(require doeff-hy.macros [do! <-])" in second.stderr


def test_a_forgotten_require_in_an_imported_module_says_the_line_to_add(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    (tmp_path / "greet.hy").write_text(
        "(import doeff [Ask])\n(setv greet (do! (<- greeting (Ask \"greeting\")) greeting))\n",
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text("import hy\nimport greet\n", encoding="utf-8")
    result = _run(tmp_path, deadline_scale_factor, "main.py")
    assert result.returncode != 0
    assert "greet.hy:2:" in result.stderr
    assert "(require doeff-hy.macros [do! <-])" in result.stderr


def test_names_from_two_providers_get_one_line_each(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    text = "(defk answer [] 42)\n(defrecord Point [x y])\n"
    result = _hy_script(tmp_path, text, deadline_scale_factor)
    assert "(require doeff-hy.macros [defk])" in result.stderr
    assert "(require doeff-hy.record [defrecord])" in result.stderr
    # 実行の時に落ちる NameError の名(mangle しない macro の名はそのまま)
    assert "NameError: name 'defk' is not defined" in result.stderr


def test_a_name_that_the_module_defines_or_imports_is_not_reported(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    # 自分の関数 do! と check を定義し、val は別の module から import する — macro と同じ名の関数を使う正しい module。
    (tmp_path / "helpers.hy").write_text("(defn val [x] x)\n", encoding="utf-8")
    text = """\
(import helpers [val])
(defn do! [x] (+ x 1))
(defn check [x] (* x 2))
(print (do! 1) (check 2) (val 3))
"""
    result = _hy_script(tmp_path, text, deadline_scale_factor)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["2 4 3"]
    assert result.stderr == ""


def test_the_correct_readme_example_prints_no_warning(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    result = _hy_script(tmp_path, CORRECT, deadline_scale_factor)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["hello"]
    assert result.stderr == ""


def test_a_macro_imported_instead_of_required_says_to_require_it(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    result = _hy_script(tmp_path, IMPORT_INSTEAD, deadline_scale_factor)
    assert result.returncode != 0
    assert "ImportError: cannot import name 'defhandler' from 'doeff_hy.handle'" in result.stderr
    assert "(require doeff-hy.handle [defhandler])" in result.stderr
    assert "main.hy:1:" in result.stderr


def test_a_plain_import_of_a_macro_module_is_not_reported(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    # module そのものを import する・macro でない名を import するのは正しい(macro の module の関数は import で入れる)。
    text = "(import doeff-hy.handle)\n(import doeff-hy.macros)\n(print \"ok\")\n"
    result = _hy_script(tmp_path, text, deadline_scale_factor)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


def test_the_hint_is_a_warning_that_python_can_turn_into_an_error(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    (tmp_path / "main.hy").write_text(FORGOT_REQUIRE, encoding="utf-8")
    result = _run(
        tmp_path,
        deadline_scale_factor,
        "-W",
        "error::UserWarning",
        "-m",
        "hy",
        "main.hy",
    )
    assert result.returncode != 0
    assert "HyMacroUseWarning" in result.stderr
    assert "NameError" not in result.stderr  # 実行に入る前に止まる


#: 表を実物と照らす提供元(table の順 — 名の提供元の探しは前から)。
HOMES = {"doeff-hy.macros": "doeff_hy.macros", "doeff-hy.handle": "doeff_hy.handle", "doeff-hy.record": "doeff_hy.record"}


@pytest.mark.parametrize("hy_name", sorted(HOMES))
def test_the_table_of_macro_names_is_what_require_star_brings(hy_name: str) -> None:
    # 表が実物の macro の表と食い違うと、新しい macro の require し忘れを黙って見逃す・無い名を勧める。
    from doeff_hy_bytecode_guard import macro_hints

    module = importlib.import_module(HOMES[hy_name])
    import hy

    real = {hy.unmangle(name) for name in records.exported_macro_names(vars(module))}
    assert set(macro_hints.MACRO_HOMES[hy_name]) == real


@pytest.mark.parametrize("hy_name", sorted(HOMES))
def test_no_name_in_the_table_is_also_a_runtime_attribute_of_its_provider(hy_name: str) -> None:
    # import で入れた名が macro と同じ名の関数なら、import は正しい(警告すると誤り)。
    from doeff_hy_bytecode_guard import macro_hints

    module = importlib.import_module(HOMES[hy_name])
    import hy

    runtime = {hy.unmangle(name) for name in vars(module)}
    assert runtime & set(macro_hints.MACRO_HOMES[hy_name]) == set()


def test_the_table_lists_the_providers_in_the_order_the_line_prefers() -> None:
    from doeff_hy_bytecode_guard import macro_hints

    assert list(macro_hints.MACRO_HOMES) == list(HOMES)
