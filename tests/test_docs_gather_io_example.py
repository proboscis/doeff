"""09 章の Gather で I/O を並べる例が、I/O を handler に置いた形で走り、書いてある通りに印字する事の検
(取り組み doeff-publish)。

09 章の小節「Using Gather with Async Operations」の例は、program の中で `yield Await(http_get(url))` と HTTP を直に
呼んでいた。doeff の書き方では、program は `HttpGet(url)` の effect を yield し、
I/O は `@handler` を付けた handler が持つ。その例が今の doeff で走り、print の注記どおりに印字する事を読む。

09 章は page 全体をまだ走らせられない(2026-10-26 の時点で 16 囲みのうち 14 が注記どおりに走らない)ので、page を
走らせる検 tests/test_docs_page_examples.py の CHECKED_PAGES に載せず、この小節だけを走らせる。page 全体を走る形に
直したら、09 章を CHECKED_PAGES に移し、この検を外す(同じ例を 2 度走らせない)。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_readme_examples import ROOT, Example, _run, examples

ADVANCED_PAGE = "docs/09-advanced-effects.md"
# 09 章の、Spawn と Gather で I/O を並べる小節。
GATHER_IO_SUBSECTION = "Using Gather with Async Operations"
_HEADING = re.compile(r"^#{2,3} (?P<title>.+?)[ \t]*$", re.MULTILINE)


def _subsection_lines(text: str, title: str) -> range:
    """text の中の、見出し title(### か ##)の小節の行の範囲(1 始まり・次の見出しの前まで)。"""
    headings = [(text.count("\n", 0, found.start()) + 1, found["title"]) for found in _HEADING.finditer(text)]
    for index, (line, found_title) in enumerate(headings):
        if found_title == title:
            end = headings[index + 1][0] if index + 1 < len(headings) else text.count("\n") + 2
            return range(line, end)
    return range(0)


def _gather_io_fences() -> list[Example]:
    """09 章の Gather で I/O を並べる小節の、走らせて注記と比べる囲み。"""
    text = (ROOT / ADVANCED_PAGE).read_text(encoding="utf-8")
    lines = _subsection_lines(text, GATHER_IO_SUBSECTION)
    return [example for example in examples.fences(text, ADVANCED_PAGE) if example.line in lines and example.runs()]


def test_the_gather_io_subsection_has_an_example_that_runs() -> None:
    # 小節の名を変えたり囲みを excerpt にしたりすると、下の検は 1 本も走らずに緑になる。
    assert _gather_io_fences(), f"{ADVANCED_PAGE} の小節 {GATHER_IO_SUBSECTION!r} に、走らせる囲みが無い"


@pytest.mark.parametrize("example", _gather_io_fences(), ids=lambda example: f"09:{example.line}")
def test_gather_io_example_runs_and_prints_what_it_states(
    example: Example, tmp_path: Path, deadline_scale_factor: float
) -> None:
    assert example.problems() == []
    mismatch = examples.mismatch(example, _run(example, tmp_path, deadline_scale_factor))
    assert mismatch is None, mismatch


@pytest.mark.parametrize("example", _gather_io_fences(), ids=lambda example: f"09:{example.line}")
def test_gather_io_example_keeps_io_out_of_the_program(example: Example) -> None:
    # program に I/O を直に焼き込む形(`Await(http_get(...))` を @do の中で yield する)が戻らない。
    # Await は handler の中だけで使う。
    assert "yield HttpGet(" in example.code
    assert "@handler" in example.code
    programs = re.split(r"^@", example.code, flags=re.MULTILINE)
    awaits_in_programs = [part for part in programs if part.startswith("do\n") and "Await(" in part]
    assert awaits_in_programs == [], "Await を yield する @do の program が在る — I/O は handler に置く"
