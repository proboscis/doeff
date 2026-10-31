"""無い key の Get を、仕様と docs が実装と同じに言う事の検(取り組み doeff-publish)。

0.5.0 の `state()` は、無い key の `Get` に None を返す(tests/test_core_effects.py の test_get_missing_returns_none)。writer と
Hy の defhandler の session val / var は、この None を「まだ置いていない」と読んで初めの値を置く。ところが仕様
docs/history/specs/effects/SPEC-EFF-002-state.md は KeyError と書き、守る検として木に無い名
(test_get_missing_key_raises_keyerror)を挙げていた。09 章の節「State Effects in Concurrent Programs」も KeyError と
言いながら、囲みは `(current or 0)` で None を前提にし、どの検も囲みを走らせていなかった(docs の報告
2026-10-17-doeff-docs-137-run.html の 09 章 L94)。

確かめる事は 2 つ:
- 09 章のその節の囲みが、README と同じ約束(tools/readme_examples.py)で走り、print の注記どおりに印字する。
- 仕様 SPEC-EFF-002 が名指す検(`tests/…py::名` の形)が、どれも木に在る。無い key の Get の行は、None を守る検を名指す。

09 章は page 全体をまだ走らせられない(2026-10-17 の時点で 16 囲みのうち 13 に注記が無い)ので、page を走らせる検
tests/test_docs_page_examples.py の CHECKED_PAGES に載せず、この節だけを走らせる。page 全体を走る形に直したら、09 章を
CHECKED_PAGES に移し、ここの節の検を外す(同じ例を 2 度走らせない)。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.test_readme_examples import ROOT, Example, _run, examples

ADVANCED_PAGE = "docs/09-advanced-effects.md"
# 09 章の、並ぶ処理の中の State の節(無い key の Get を説明し、例を持つ)。
STATE_SECTION = "State Effects in Concurrent Programs"
STATE_SPEC = ROOT / "docs" / "history" / "specs" / "effects" / "SPEC-EFF-002-state.md"
# 無い key の Get が None を返す事を守る検(仕様はこれを名指す)。
MISSING_KEY_TEST = "tests/test_core_effects.py::TestState::test_get_missing_returns_none"
# 仕様の中の、検の名指し(`tests/test_core_effects.py::TestState::test_get_put` の形)。
_CITED_TEST = re.compile(r"`(?P<node>(?:tests|packages)/[^`\s:]+\.py(?:::[A-Za-z_][A-Za-z0-9_]*)+)`")
# path の無い検の名(`test_get_put`)— どの file の物か読めず、在るかを確かめられない。
_BARE_TEST_NAME = re.compile(r"`(?P<name>test_[A-Za-z0-9_]+)`")


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


def _defines(path: Path, names: list[str]) -> bool:
    """path の module が、names の列(class の中の関数なら class・関数の順)を定義しているか。"""
    scope: list[ast.stmt] = ast.parse(path.read_text(encoding="utf-8")).body
    for name in names:
        found = next(
            (
                node
                for node in scope
                if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name == name
            ),
            None,
        )
        if found is None:
            return False
        scope = found.body
    return True


def _cited_tests() -> list[str]:
    """仕様 SPEC-EFF-002 が名指す検(path::名)。"""
    return [found["node"] for found in _CITED_TEST.finditer(STATE_SPEC.read_text(encoding="utf-8"))]


def test_state_spec_cites_the_test_that_guards_the_missing_key_get() -> None:
    assert MISSING_KEY_TEST in _cited_tests(), (
        f"{STATE_SPEC.relative_to(ROOT)} が、無い key の Get を守る検 {MISSING_KEY_TEST} を名指していない"
    )


def test_every_test_the_state_spec_cites_exists() -> None:
    missing = [
        node
        for node in _cited_tests()
        if not (ROOT / node.split("::")[0]).is_file()
        or not _defines(ROOT / node.split("::")[0], node.split("::")[1:])
    ]
    assert missing == [], f"{STATE_SPEC.relative_to(ROOT)} が、木に無い検を名指す: {missing}"


def test_the_state_spec_names_tests_with_their_path() -> None:
    bare = [found["name"] for found in _BARE_TEST_NAME.finditer(STATE_SPEC.read_text(encoding="utf-8"))]
    assert bare == [], (
        f"{STATE_SPEC.relative_to(ROOT)} が、path の無い検の名 {bare} を挙げる — "
        "`tests/<file>.py::<class>::<名>` の形で書く"
    )
