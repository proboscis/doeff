"""docs の入門の道の page の例が今の doeff でそのまま走り、書いてある通りに印字する事の検(取り組み doeff-publish・card ki-9c93a506326b)。

初めての利用者は README から docs の目次(docs/index.md)へ進み、入門の章から順に読む。その道の page の例は、どの検も
走らせていなかったので、handler の使い方が変わっても誰も気づかなかった: 0.5.0 の時点で、目次から辿る囲み 324 個のうち
158 個が落ち、入門の章で 13 個中 5 個・エラー処理の章で 12 個中 10 個が古い使い方だった(writer が state の handler を要る・
Try が try_handler を要る — 下の STALE_EXAMPLES が当時の写し・docs の報告 2026-10-17-doeff-docs-examples-run.html)。

囲みは README と同じ約束で読み・走らせ・注記と比べる(約束の 1 点は tools/readme_examples.py — 読み込みと走らせ方は
tests/test_readme_examples.py の物を借りる)。走らせない断片は、言葉の後に語 `excerpt` を置く(```` ```python excerpt ```` —
card ki-9c49b3d5867a が 13 章に付けた印と同じ)。page は 1 つずつ別の dir で扱い、`file=` の囲みは後の囲みの前に置く。

入門の道 = 目次の page・目次の Learning Path の For Beginners の page・card の言う 01〜05 の章。14 章(CLI の自動の発見)は
入れない: 例は doeff-indexer(核の 5 個の外)と、dir を持つ package(`myapp/features/report.py`)を要り、約束の `file=` は
dir を含まない名だけを書ける。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import tomllib

from tests.test_readme_examples import ROOT, Example, _run, examples

DOCS = ROOT / "docs"
# この検の file(repo の根からの path — pyproject.toml の契約の表の書き方)。
THIS_CHECK = Path(__file__).resolve().relative_to(ROOT).as_posix()
# 入門の道の page(docs/ からの名・読む順)。目次の For Beginners に page を足す時は、ここにも足す(下の検が名指す)。
ENTRY_PATH = (
    "index.md",
    "01-getting-started.md",
    "02-core-concepts.md",
    "03-basic-effects.md",
    "04-async-effects.md",
    "05-error-handling.md",
)
# 目次の中の、初めての利用者が読む順の節の見出し(この節の link の page は全部 ENTRY_PATH に在る)。
BEGINNER_SECTION = "### For Beginners"
# 目次の節の中の、docs/ の page への link(`[名](01-getting-started.md)`・`#節` つきも)。
_PAGE_LINK = re.compile(r"\]\((?P<page>[^)#/:]+\.md)(?:#[^)]*)?\)")


def _fences(page: str) -> list[Example]:
    """page の囲み(検の名の page は repo の根からの path)。"""
    return examples.fences((DOCS / page).read_text(encoding="utf-8"), f"docs/{page}")


def _runnable() -> list[tuple[str, Example]]:
    """入門の道の、走らせて注記と比べる囲み(page と組にする)。"""
    return [(page, example) for page in ENTRY_PATH for example in _fences(page) if example.runs()]


def _example_id(case: tuple[str, Example]) -> str:
    """parametrize の id — page と行で、落ちた検がどの囲みかを読めるようにする。"""
    page, example = case
    return f"{Path(page).stem}:{example.line}"


@pytest.mark.parametrize("case", _runnable(), ids=_example_id)
def test_entry_path_example_runs_and_prints_what_it_states(
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


@pytest.mark.parametrize("page", ENTRY_PATH)
def test_entry_path_fences_keep_the_rules(page: str) -> None:
    problems = [problem for example in _fences(page) for problem in example.problems()]
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("page", ENTRY_PATH)
def test_entry_path_page_has_an_example_that_runs(page: str) -> None:
    # 全部を excerpt にした page は、何も確かめていない(印を付けて検から外すだけで緑になる)。
    assert any(example.runs() for example in _fences(page)), f"docs/{page} に、走らせる例が無い"


def _beginner_pages(index: str) -> list[str]:
    """目次の For Beginners の節が link する page(docs/ からの名)。"""
    _, found, rest = index.partition(f"\n{BEGINNER_SECTION}\n")
    assert found, (
        f"docs/index.md に節 {BEGINNER_SECTION!r} が無い — 節の名を変えたら BEGINNER_SECTION も直す"
    )
    section = re.split(r"^#{1,3} ", rest, maxsplit=1, flags=re.MULTILINE)[0]
    return [link["page"] for link in _PAGE_LINK.finditer(section)]


def test_entry_path_covers_the_beginner_pages_of_the_index() -> None:
    pages = _beginner_pages((DOCS / "index.md").read_text(encoding="utf-8"))
    assert pages, f"docs/index.md の {BEGINNER_SECTION} に、page への link が無い"
    assert [page for page in pages if page not in ENTRY_PATH] == [], (
        f"docs/index.md の {BEGINNER_SECTION} が link する page が ENTRY_PATH に無い — 足して例を走らせる"
    )


def test_contract_table_selects_this_check_for_each_entry_page() -> None:
    # page を書き換えた便の `make test-changed` がこの検を選ぶ(例は file に書いて走らせるので、import の逆依存では選ばれない)。
    rows = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["doeff"][
        "contract-tests"
    ]
    selected = {row["prefix"] for row in rows if THIS_CHECK in row.get("tests", [])}
    assert [page for page in ENTRY_PATH if f"docs/{page}" not in selected] == [], (
        f"pyproject.toml の [[tool.doeff.contract-tests]] に、{THIS_CHECK} を選ぶ行の無い入門の道の page が在る"
    )


def test_beginner_pages_are_read_from_the_section_only() -> None:
    index = """## Learning Path

### For Beginners

1. Start with **[Getting Started](01-getting-started.md)** for basics
2. Then **[Errors](05-error-handling.md#try-effect)**, see [GitHub](https://github.com/x/y.md)

### For Intermediate Users

1. **[Async Effects](04-async-effects.md)**
"""
    assert _beginner_pages(index) == ["01-getting-started.md", "05-error-handling.md"]


# 直す前の docs の 2 つの例の写し(この検が名指す事を確かめる失敗ケース)— 03 章の Ask の例(writer の外に state が無い・
# card ki-9c93a506326b の前)と 05 章の Try の例(try_handler が無い・card ki-74ffd7faafe8 の前)。
STALE_EXAMPLES = {
    "Get": Example(
        "docs/03-basic-effects.md",
        "Reader Effects",
        "python",
        """from doeff import do, run
from doeff_core_effects import Ask, Tell
from doeff_core_effects._handlers import reader, writer
from doeff_core_effects._scheduler import scheduled

@do
def connect_to_database():
    db_url = yield Ask("database_url")
    timeout = yield Ask("timeout")
    yield Tell(f"Connecting to {db_url} with timeout {timeout}")
    return f"Connected to {db_url}"

# Run with environment
def main():
    prog = connect_to_database()
    prog = writer(prog)
    prog = reader(env={
        "database_url": "postgresql://localhost/mydb",
        "timeout": 30
    })(prog)
    result = run(scheduled(prog))
    print(result)  # "Connected to postgresql://localhost/mydb"

main()
""",
        21,
    ),
    "Try": Example(
        "docs/05-error-handling.md",
        "Try Effect",
        "python",
        """from doeff import Err, Ok, Try, Tell, do, run
from doeff_core_effects._handlers import writer

@do
def parse_count(raw: str):
    return int(raw)

@do
def app(raw: str):
    parsed = yield Try(parse_count(raw))

    match parsed:
        case Ok(value=count):
            yield Tell(f"parsed count={count}")
            return count
        case Err(error=exc):
            yield Tell(f"parse failed: {exc}")
            return 0

prog = app("not-a-number")
prog = writer(prog)
result = run(prog)
print(result)  # 0
""",
        110,
    ),
}


@pytest.mark.parametrize("effect", sorted(STALE_EXAMPLES))
def test_check_names_the_stale_examples(
    effect: str, tmp_path: Path, deadline_scale_factor: float
) -> None:
    example = STALE_EXAMPLES[effect]
    mismatch = examples.mismatch(example, _run(example, tmp_path, deadline_scale_factor))
    assert mismatch is not None
    assert "落ちた" in mismatch
    assert f"UnhandledEffect: no handler found for effect: {effect}" in mismatch
