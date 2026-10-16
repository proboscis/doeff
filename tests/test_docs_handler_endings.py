"""Resume しない handler と Pass しない handler が program をどう終えるかを、docs が走る例で言う事の検
(取り組み doeff-publish・card ki-03bb9225a242)。

自作の handler が Resume せずに値を return すると、handler を置いた program の残りは走らず、その program の答えは
handler の値になる。注記も Pass も無い handler は、扱わない effect(Ask など)で終わりまで落ちて None を返し、program を
None で終える。どちらも誤りも警告も出ない — VM は「再開しないで値を返す」を正当な終わり方として受け、わざと止めた
のか Resume を書き忘れたのかを見分ける印を持たないため(ADR-DOE-CORE-EFFECTS-003 R15・SPEC-TYPES-001 の HP-04)。
0.5.0 と main 4685dc801d62 で、書いた人が待つ `body=got …` の代わりに `got https://example.com` と `None` が出た
(誤りの表 2026-10-17-doeff-first-errors-20.html の 16 と付録)。

確かめる事は 2 つ:
- 13 章の Control Effects の節(Resume と Pass の項を持つ)の囲みが、README と同じ約束(tools/readme_examples.py)で
  走り、print の注記どおりに印字する。
- 02 章の Handler Contract の節に、走る囲みが在る(その囲みを走らせるのは入門の道の検
  tests/test_docs_entry_path_examples.py — ここでは走らせない。同じ例を 2 度走らせない)。

13 章は page 全体をまだ走らせられない(2026-10-17 の時点で、走らせる言葉の囲みの多くに注記が無い)ので、page を
走らせる検 tests/test_docs_page_examples.py の CHECKED_PAGES に載せず、この節だけを走らせる。page 全体を走る形に
直したら、13 章を CHECKED_PAGES に移し、ここの 13 章の節の検を外す。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_readme_examples import ROOT, Example, _run, examples

API_PAGE = "docs/13-api-reference.md"
# 13 章の、Resume と Pass の項を持つ節。
CONTROL_SECTION = "Control Effects"
CORE_PAGE = "docs/02-core-concepts.md"
# 02 章の、Resume しない return と Pass しない handler の小節を持つ節。
HANDLER_SECTION = "Handler Contract"


def _section_fences(page: str, section: str) -> list[Example]:
    """page の section の、走らせて注記と比べる囲み。"""
    fences = examples.fences((ROOT / page).read_text(encoding="utf-8"), page)
    return [example for example in fences if example.section == section and example.runs()]


def test_the_control_section_has_examples_that_run() -> None:
    # 節の名を変えたり囲みを全部 excerpt にしたりすると、下の検は 1 本も走らずに緑になる。
    assert len(_section_fences(API_PAGE, CONTROL_SECTION)) >= 2, (
        f"{API_PAGE} の節 {CONTROL_SECTION!r} に、Resume と Pass の項の走らせる囲みが無い"
    )


def test_the_handler_contract_section_has_examples_that_run() -> None:
    # 走らせるのは入門の道の検。ここでは、節の名の変更や excerpt で 2 つの小節の例が検から外れない事だけを読む。
    assert len(_section_fences(CORE_PAGE, HANDLER_SECTION)) >= 2, (
        f"{CORE_PAGE} の節 {HANDLER_SECTION!r} に、Resume しない return と Pass しない handler の走らせる囲みが無い"
    )


@pytest.mark.parametrize(
    "example",
    _section_fences(API_PAGE, CONTROL_SECTION),
    ids=lambda example: f"13:{example.line}",
)
def test_control_section_example_runs_and_prints_what_it_states(
    example: Example, tmp_path: Path, deadline_scale_factor: float
) -> None:
    assert example.problems() == []
    mismatch = examples.mismatch(example, _run(example, tmp_path, deadline_scale_factor))
    assert mismatch is None, mismatch
