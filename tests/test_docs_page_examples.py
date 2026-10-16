"""入門の道の外の docs の page のうち、例を走る形に直した page の例が今の doeff でそのまま走り、書いてある通りに印字する事の検
(取り組み doeff-publish・card ki-8e45f740dc61)。

入門の道(目次と 01〜05 章)の page は tests/test_docs_entry_path_examples.py が走らせる。その外の page の例は、どの検も
走らせていなかった: 0.5.0 の時点で、定義だけで通った囲み 137 個を run まで走らせると 25 個が落ちた(137 個の報告
2026-10-17-doeff-docs-137-run.html)。07 章の API Response Caching の例は、同期の httpx.get の返りを Await に渡していた。

囲みは入門の道と同じ約束で読み・走らせ・注記と比べる(約束の 1 点は tools/readme_examples.py)。走らせない断片は、言葉の
後に語 `excerpt` を置く。page の例を走る形に直したら、その page を CHECKED_PAGES と pyproject.toml の契約の表の両方に足す。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomllib

from tests.test_docs_entry_path_examples import ENTRY_PATH, _fences
from tests.test_readme_examples import ROOT, Example, _run, examples

# この検の file(repo の根からの path — pyproject.toml の契約の表の書き方)。
THIS_CHECK = Path(__file__).resolve().relative_to(ROOT).as_posix()
# 例を走る形に直した、入門の道の外の page(docs/ からの名)。
CHECKED_PAGES = ("07-cache-system.md",)


def _runnable() -> list[tuple[str, Example]]:
    """CHECKED_PAGES の、走らせて注記と比べる囲み(page と組にする)。"""
    return [
        (page, example) for page in CHECKED_PAGES for example in _fences(page) if example.runs()
    ]


def _example_id(case: tuple[str, Example]) -> str:
    """parametrize の id — page と行で、落ちた検がどの囲みかを読めるようにする。"""
    page, example = case
    return f"{Path(page).stem}:{example.line}"


@pytest.mark.parametrize("case", _runnable(), ids=_example_id)
def test_page_example_runs_and_prints_what_it_states(
    case: tuple[str, Example], tmp_path: Path, deadline_scale_factor: float
) -> None:
    page, example = case
    assert example.problems() == []
    # 同じ page の前の囲みが書く file(`file=` の module など)を、読み手と同じく先に置く。
    for earlier in _fences(page):
        if earlier.line < example.line and earlier.written_as() is not None:
            examples.write(earlier, tmp_path)
    mismatch = examples.mismatch(example, _run(example, tmp_path, deadline_scale_factor))
    assert mismatch is None, mismatch


@pytest.mark.parametrize("page", CHECKED_PAGES)
def test_page_fences_keep_the_rules(page: str) -> None:
    problems = [problem for example in _fences(page) for problem in example.problems()]
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("page", CHECKED_PAGES)
def test_page_has_an_example_that_runs(page: str) -> None:
    # 全部を excerpt にした page は、何も確かめていない(印を付けて検から外すだけで緑になる)。
    assert any(example.runs() for example in _fences(page)), f"docs/{page} に、走らせる例が無い"


def test_checked_pages_are_outside_the_entry_path() -> None:
    # 入門の道の page は tests/test_docs_entry_path_examples.py が走らせる(同じ例を 2 度走らせない)。
    assert [page for page in CHECKED_PAGES if page in ENTRY_PATH] == []


def test_contract_table_selects_this_check_for_each_checked_page() -> None:
    # page を書き換えた便の `make test-changed` がこの検を選ぶ(例は file に書いて走らせるので、import の逆依存では選ばれない)。
    rows = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["doeff"][
        "contract-tests"
    ]
    selected = {row["prefix"] for row in rows if THIS_CHECK in row.get("tests", [])}
    assert [page for page in CHECKED_PAGES if f"docs/{page}" not in selected] == [], (
        f"pyproject.toml の [[tool.doeff.contract-tests]] に、{THIS_CHECK} を選ぶ行の無い page が在る"
    )
