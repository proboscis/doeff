"""入門の道の外の docs の page のうち、例を走る形に直した page の例が今の doeff でそのまま走り、書いてある通りに印字する事の検
(取り組み doeff-publish・card ki-8e45f740dc61)。

入門の道(目次と 01〜05 章)の page は tests/test_docs_entry_path_examples.py が走らせる。その外の page の例は、どの検も
走らせていなかった: 0.5.0 の時点で、定義だけで通った囲み 137 個を run まで走らせると 25 個が落ちた(137 個の報告
2026-10-17-doeff-docs-137-run.html)。07 章の API Response Caching の例は、同期の httpx.get の返りを Await に渡していた。

囲みは入門の道と同じ約束で読み・走らせ・注記と比べる(約束の 1 点は tools/readme_examples.py)。走らせない断片は、言葉の
後に語 `excerpt` を置く。page の例を走る形に直したら、pyproject.toml の契約の表に、その page でこの検を選ぶ行を 1 つ足す。
走らせる page の一覧(CHECKED_PAGES)はその表から読む — この file に一覧を書いていた間は、並んで page を足す 2 つの commit が
同じ 1 行で衝突した(着地の列 L2431・L2432・card ki-7277427f6d8e)。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomllib

from tests.test_docs_entry_path_examples import ENTRY_PATH, _fences
from tests.test_readme_examples import ROOT, Example, _run, examples

# この検の file(repo の根からの path — pyproject.toml の契約の表の書き方)。
THIS_CHECK = Path(__file__).resolve().relative_to(ROOT).as_posix()
# docs の page の接頭辞(契約の表の prefix の書き方)。
DOCS_PREFIX = "docs/"


def prefixes_selecting(check: str) -> list[str]:
    """pyproject.toml の契約の表で、check(repo の根からの path)を選ぶ行の接頭辞(表の順)。"""
    rows = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["doeff"][
        "contract-tests"
    ]
    return [row["prefix"] for row in rows if check in row.get("tests", [])]


def _prefixes_selecting_this_check() -> list[str]:
    """pyproject.toml の契約の表で、この検を選ぶ行の接頭辞(表の順)。"""
    return prefixes_selecting(THIS_CHECK)


# 例を走る形に直した、入門の道の外の page(docs/ からの名)— 契約の表の、この検を選ぶ行が 1 点の定義。
CHECKED_PAGES = tuple(
    prefix.removeprefix(DOCS_PREFIX) for prefix in _prefixes_selecting_this_check()
)


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


def prefixes_that_are_not_pages(prefixes: list[str]) -> list[str]:
    """接頭辞のうち、在る docs の page(docs/<名>.md の file)でない物(dir・書き間違い)。"""
    return [
        prefix
        for prefix in prefixes
        if not (
            prefix.startswith(DOCS_PREFIX) and prefix.endswith(".md") and (ROOT / prefix).is_file()
        )
    ]


def test_rows_selecting_this_check_name_docs_pages() -> None:
    # 表の行が page を変えた commit の `make test-changed` にこの検を選ばせ、同じ行が走らせる page を決める。行の接頭辞が
    # page でないなら、その page の例はどの検も走らせない。
    prefixes = _prefixes_selecting_this_check()
    assert prefixes, f"pyproject.toml の [[tool.doeff.contract-tests]] に、{THIS_CHECK} を選ぶ行が無い"
    not_pages = prefixes_that_are_not_pages(prefixes)
    assert not_pages == [], (
        f"pyproject.toml の [[tool.doeff.contract-tests]] で {THIS_CHECK} を選ぶ行の接頭辞 {not_pages} が、"
        f"在る docs の page({DOCS_PREFIX}<名>.md)でない"
    )


def test_the_check_names_prefixes_that_are_not_pages() -> None:
    sample = ["docs/07-cache-system.md", "docs/", "docs/no-such-page.md", "README.md"]
    assert prefixes_that_are_not_pages(sample) == ["docs/", "docs/no-such-page.md", "README.md"]
