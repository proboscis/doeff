"""11 章(docs/11-kleisli-arrows.md)の節「Arguments Are Passed As Given」の例が今の doeff でそのまま走り、書いてある通りに
印字する事の検(取り組み doeff-publish・card ki-7277427f6d8e)。

11 章は、@do が引数の注記から should_unwrap を決め、effect の引数を走る時に解く(`x: int` の `Ask("x")` は `Perform` になる)と
書いていた。doeff は 2026-03-25 の VM の書き直し(926341cc0 が doeff/kleisli.py の解きの仕組みを消した)から引数を解かず、
`Call` に呼んだ時のまま入れる。0.5.0 で章の例を run すると、本文の言う "5:10" でなく "Ask('x'):10" が返った(137 個の報告
2026-10-17-doeff-docs-137-run.html の 11 章 L102 — 下の OLD_EXAMPLE が当時の写しに run を足した物)。

節の囲みは README と同じ約束で読み・走らせ・注記と比べる(約束の 1 点は tools/readme_examples.py — 読み込みと走らせ方は
tests/test_readme_examples.py の物を借りる)。旧い説明の語(should_unwrap・auto-unwrap)が章に戻らない事も読む。
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from tests.test_readme_examples import ROOT, Example, _run, examples

PAGE = "docs/11-kleisli-arrows.md"
# 引数の渡り方を言う節の見出し(`## ` の後)。節の名を変えたら、ここも直す。
SECTION = "Arguments Are Passed As Given"
# この検の file(repo の根からの path — pyproject.toml の契約の表の書き方)。
THIS_CHECK = Path(__file__).resolve().relative_to(ROOT).as_posix()
# 旧い説明の語 — 注記から引数を解くかを決める旗と、その仕組みの名。
OLD_POLICY_WORDS = re.compile(r"should_unwrap|auto[- ]?unwrap", re.IGNORECASE)


def _section_fences() -> list[Example]:
    """節の囲み(検の名の page は repo の根からの path)。"""
    text = (ROOT / PAGE).read_text(encoding="utf-8")
    return [example for example in examples.fences(text, PAGE) if example.section == SECTION]


def _example_id(example: Example) -> str:
    """parametrize の id — 行で、落ちた検がどの囲みかを読めるようにする。"""
    return f"L{example.line}"


@pytest.mark.parametrize(
    "example", [example for example in _section_fences() if example.runs()], ids=_example_id
)
def test_arguments_example_runs_and_prints_what_it_states(
    example: Example, tmp_path: Path, deadline_scale_factor: float
) -> None:
    assert example.problems() == []
    mismatch = examples.mismatch(example, _run(example, tmp_path, deadline_scale_factor))
    assert mismatch is None, mismatch


def test_arguments_section_has_examples_that_run() -> None:
    # 囲みを全部 excerpt にした節は、何も確かめていない(印を付けて検から外すだけで緑になる)。
    assert any(example.runs() for example in _section_fences()), (
        f"{PAGE} の節 {SECTION!r} に、走らせる例が無い — 節の名を変えたら SECTION も直す"
    )


def test_chapter_does_not_describe_annotation_driven_unwrap() -> None:
    lines = (ROOT / PAGE).read_text(encoding="utf-8").splitlines()
    found = [number for number, line in enumerate(lines, start=1) if OLD_POLICY_WORDS.search(line)]
    assert found == [], (
        f"{PAGE} の行 {found} が、今の @do に無い引数の自動の解き(should_unwrap)を言う — @do は引数をそのまま渡す"
    )


def test_contract_table_selects_this_check_for_the_chapter() -> None:
    # 章を書き換えた便の `make test-changed` がこの検を選ぶ(例は file に書いて走らせるので、import の逆依存では選ばれない)。
    rows = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["doeff"][
        "contract-tests"
    ]
    selected = {row["prefix"] for row in rows if THIS_CHECK in row.get("tests", [])}
    assert PAGE in selected, (
        f"pyproject.toml の [[tool.doeff.contract-tests]] に、{PAGE} で {THIS_CHECK} を選ぶ行が無い"
    )


# 直す前の章の例の写し(L102)に、本文の言う値を注記した run の 1 行を足した物 — この検が名指す事を確かめる失敗ケース。
OLD_EXAMPLE = Example(
    PAGE,
    "Annotation-Aware Argument Classification",
    "python",
    """from doeff import Ask, Pure, do
from doeff import Program

@do
def use_values(x: int, raw_program: Program):
    y = yield raw_program
    return f"{x}:{y}"

call_expr = use_values(Ask("x"), Pure(10))
# x: int -> should_unwrap=True -> Ask("x") becomes Perform(Ask("x"))
# raw_program: Program -> should_unwrap=False -> Pure(10) becomes Pure(...)

from doeff import run
from doeff_core_effects.handlers import reader
print(run(reader(env={"x": 5})(call_expr)))  # 5:10
""",
    102,
)


def test_check_names_the_old_example(tmp_path: Path, deadline_scale_factor: float) -> None:
    mismatch = examples.mismatch(OLD_EXAMPLE, _run(OLD_EXAMPLE, tmp_path, deadline_scale_factor))
    assert mismatch is not None
    assert "印字 [\"Ask('x'):10\"]" in mismatch
    assert OLD_POLICY_WORDS.search(OLD_EXAMPLE.code) is not None
