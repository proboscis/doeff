"""無い key の Get を、docs が実装と同じに言う事の検(取り組み doeff-publish・card ki-4fc77b6d6aa9)。

0.5.0 の `state()` は、無い key の `Get` に None を返す(tests/test_core_effects.py の test_get_missing_returns_none)。writer と
Hy の defhandler の session val / var は、この None を「まだ置いていない」と読んで初めの値を置く。09 章の節「State Effects in
Concurrent Programs」は KeyError と言いながら、囲みは `(current or 0)` で None を前提にし、どの検も囲みを走らせていなかった。

確かめる事: 09 章のその節の囲みが、README と同じ約束(tools/readme_examples.py)で走り、print の注記どおりに印字する。

09 章は page 全体をまだ走らせられない(2026-10-17 の時点で 16 囲みのうち 13 に注記が無い)ので、page を走らせる検
tests/test_docs_page_examples.py の CHECKED_PAGES に載せず、この節だけを走らせる。page 全体を走る形に直したら、09 章を
CHECKED_PAGES に移し、ここの節の検を外す(同じ例を 2 度走らせない)。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_readme_examples import ROOT, Example, _run, examples

ADVANCED_PAGE = "docs/09-advanced-effects.md"
# 09 章の、並ぶ処理の中の State の節(無い key の Get を説明し、例を持つ)。
STATE_SECTION = "State Effects in Concurrent Programs"


def _state_fences() -> list[Example]:
    """09 章の State の節の、走らせて注記と比べる囲み。"""
    fences = examples.fences((ROOT / ADVANCED_PAGE).read_text(encoding="utf-8"), ADVANCED_PAGE)
    return [example for example in fences if example.section == STATE_SECTION and example.runs()]


def test_the_state_section_has_examples_that_run() -> None:
    # 節の名を変えたり囲みを全部 excerpt にしたりすると、下の検は 1 本も走らずに緑になる。
    assert _state_fences(), f"{ADVANCED_PAGE} の節 {STATE_SECTION!r} に、走らせる囲みが無い"


@pytest.mark.parametrize("example", _state_fences(), ids=lambda example: f"09:{example.line}")
def test_state_section_example_runs_and_prints_what_it_states(
    example: Example, tmp_path: Path, deadline_scale_factor: float
) -> None:
    assert example.problems() == []
    mismatch = examples.mismatch(example, _run(example, tmp_path, deadline_scale_factor))
    assert mismatch is None, mismatch
