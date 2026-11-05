"""Test that chapter 11 (docs/11-kleisli-arrows.md) does not describe automatic argument unwrapping that today's @do lacks (initiative doeff-publish).

Chapter 11 said that @do decides should_unwrap from the argument annotations and unwraps effect arguments at run time (`Ask("x")` for `x: int` becomes `Perform`).
Since the 2026-03-25 rewrite of the VM (926341cc0 removed the unwrapping machinery in doeff/kleisli.py), doeff does not unwrap arguments and
puts them into `Call` as they were passed. Running the chapter's example on 0.5.0 returned "Ask('x'):10" instead of the "5:10" the text states (chapter 11 L102 of the 137-item report
2026-10-17-doeff-docs-137-run.html -- OLD_EXAMPLE below is the copy of that time with a run added).

Running the chapter's examples and comparing the printed output with the annotations is tests/test_docs_page_examples.py (the contract table has a row for chapter 11). This test
reads, by the words of the old explanation (should_unwrap, auto-unwrap), whether the old explanation returns in sentences that carry no example.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from tests.test_readme_examples import ROOT, Example, _run, examples

PAGE = "docs/11-kleisli-arrows.md"
# This test's file (path from the repo root -- the way the contract table in pyproject.toml is written).
THIS_CHECK = Path(__file__).resolve().relative_to(ROOT).as_posix()
# Words of the old explanation -- the flag that decides whether to unwrap arguments from annotations, and the name of that machinery.
OLD_POLICY_WORDS = re.compile(r"should_unwrap|auto[- ]?unwrap", re.IGNORECASE)


def old_policy_lines(text: str) -> list[int]:
    """Numbers of the lines in text that contain the words of the old explanation."""
    return [
        number
        for number, line in enumerate(text.splitlines(), start=1)
        if OLD_POLICY_WORDS.search(line)
    ]


def test_chapter_does_not_describe_annotation_driven_unwrap() -> None:
    found = old_policy_lines((ROOT / PAGE).read_text(encoding="utf-8"))
    assert found == [], (
        f"{PAGE} line {found} describes automatic argument unwrapping (should_unwrap) that today's @do lacks -- @do passes arguments through as they are"
    )


def test_contract_table_selects_this_check_for_the_chapter() -> None:
    # `make test-changed` on a commit that rewrote the chapter selects this test (it reads sentences, so it is not selected by reverse import dependencies).
    rows = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["doeff"][
        "contract-tests"
    ]
    selected = {row["prefix"] for row in rows if THIS_CHECK in row.get("tests", [])}
    assert PAGE in selected, (
        f"pyproject.toml's [[tool.doeff.contract-tests]] has no row selecting {THIS_CHECK} for {PAGE}"
    )


# A copy of the chapter's example before the fix (L102), with one run line added that annotates the value the text states -- a failure case confirming that both tests name it.
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
    # The example test names that the old example prints the Ask object instead of the text's value.
    mismatch = examples.mismatch(OLD_EXAMPLE, _run(OLD_EXAMPLE, tmp_path, deadline_scale_factor))
    assert mismatch is not None
    assert "printed [\"Ask('x'):10\"]" in mismatch
    # The word test names the two annotation lines of the old example.
    assert old_policy_lines(OLD_EXAMPLE.code) == [10, 11]
