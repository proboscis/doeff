"""11 章(docs/11-kleisli-arrows.md)が、今の @do に無い引数の自動の解きを言わない事の検(取り組み doeff-publish)。

11 章は、@do が引数の注記から should_unwrap を決め、effect の引数を走る時に解く(`x: int` の `Ask("x")` は `Perform` になる)と
書いていた。doeff は 2026-03-25 の VM の書き直し(926341cc0 が doeff/kleisli.py の解きの仕組みを消した)から引数を解かず、
`Call` に呼んだ時のまま入れる。0.5.0 で章の例を run すると、本文の言う "5:10" でなく "Ask('x'):10" が返った(137 個の報告
2026-10-17-doeff-docs-137-run.html の 11 章 L102 — 下の OLD_EXAMPLE が当時の写しに run を足した物)。

章の例を走らせて印字を注記と比べるのは tests/test_docs_page_examples.py(契約の表に 11 章の行が在る)。この検は、
例を伴わない文で旧い説明が戻る事を、旧い説明の語(should_unwrap・auto-unwrap)で読む。
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from tests.test_readme_examples import ROOT, Example, _run, examples

PAGE = "docs/11-kleisli-arrows.md"
# この検の file(repo の根からの path — pyproject.toml の契約の表の書き方)。
THIS_CHECK = Path(__file__).resolve().relative_to(ROOT).as_posix()
# 旧い説明の語 — 注記から引数を解くかを決める旗と、その仕組みの名。
OLD_POLICY_WORDS = re.compile(r"should_unwrap|auto[- ]?unwrap", re.IGNORECASE)


def old_policy_lines(text: str) -> list[int]:
    """text のうち、旧い説明の語を含む行の番号。"""
    return [
        number
        for number, line in enumerate(text.splitlines(), start=1)
        if OLD_POLICY_WORDS.search(line)
    ]


def test_chapter_does_not_describe_annotation_driven_unwrap() -> None:
    found = old_policy_lines((ROOT / PAGE).read_text(encoding="utf-8"))
    assert found == [], (
        f"{PAGE} の行 {found} が、今の @do に無い引数の自動の解き(should_unwrap)を言う — @do は引数をそのまま渡す"
    )


def test_contract_table_selects_this_check_for_the_chapter() -> None:
    # 章を書き換えた commit の `make test-changed` がこの検を選ぶ(文を読む検で、import の逆依存では選ばれない)。
    rows = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["doeff"][
        "contract-tests"
    ]
    selected = {row["prefix"] for row in rows if THIS_CHECK in row.get("tests", [])}
    assert PAGE in selected, (
        f"pyproject.toml の [[tool.doeff.contract-tests]] に、{PAGE} で {THIS_CHECK} を選ぶ行が無い"
    )


# 直す前の章の例の写し(L102)に、本文の言う値を注記した run の 1 行を足した物 — 2 つの検が名指す事を確かめる失敗ケース。
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
from doeff_core_effects._handlers import reader
print(run(reader(env={"x": 5})(call_expr)))  # 5:10
""",
    102,
)


def test_checks_name_the_old_example(tmp_path: Path, deadline_scale_factor: float) -> None:
    # 例の検は、旧い例が本文の値でなく Ask の object を印字する事を名指す。
    mismatch = examples.mismatch(OLD_EXAMPLE, _run(OLD_EXAMPLE, tmp_path, deadline_scale_factor))
    assert mismatch is not None
    assert "印字 [\"Ask('x'):10\"]" in mismatch
    # 語の検は、旧い例の注記の 2 行を名指す。
    assert old_policy_lines(OLD_EXAMPLE.code) == [10, 11]
