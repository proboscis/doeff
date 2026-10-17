"""docs と README が名指す LLM の effect が、今の doeff-llm に在る事の検(取り組み doeff-publish・card ki-d20475e94d87 の見つけた不具合)。

`LLMStructuredOutput` は `LLMStructuredQuery` に改名され(docs/llm_unified_effects.md が記す)、旧い名は今の doeff-llm に無い。
改名の後も docs/20-why-effects-over-di.md の `from doeff_llm.effects import LLMChat, LLMStructuredOutput` と、3 つの provider の
README(doeff-openai・doeff-openrouter・doeff-gemini)の対応する effect の一覧が旧い名のまま残り、写して動かすと ImportError になった。

この検は docs/*.md と README(根と各 package の)に出る `LLMChat`・`LLMStreaming*`・`LLMStructured*`・`LLMEmbed*` の名を全部集め、
`doeff_llm.effects` の `__all__` に在る事を確かめる。改名を言う行(「renamed」を含む行)と、改名の記録の表(documentation-fix-table.md)・
CHANGELOG は、旧い名を書くのが役目なので読まない。
doeff-llm は核の外の部品なので、入っていない環境(核の 5 個だけを入れる CI の Python Compatibility の venv)では、doeff-llm を読む
page ごとの検を skip にする(top で import すると集めの段で ModuleNotFoundError になり、main の CI が ERROR で赤になった —
2026-10-17)。名の拾い方の検は doeff-llm を読まないので、どの環境でも走る。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
# 旧い名を書くのが役目の file(改名の記録の表)。CHANGELOG は docs/ にも README にも無いので読まない。
RECORDS = frozenset({"documentation-fix-table.md"})
# doeff-llm の effect の名の形(`LLMService` のような DI の見本の class は含まない)。
EFFECT_NAME = re.compile(r"\bLLM(?:Chat|Streaming\w*|Structured\w*|Embed\w*)\b")
RENAME_LINE = re.compile(r"renamed", re.IGNORECASE)


def _pages() -> list[Path]:
    docs = [page for page in sorted((REPO / "docs").glob("*.md")) if page.name not in RECORDS]
    readmes = [REPO / "README.md", *sorted((REPO / "packages").glob("*/README.md"))]
    return [*docs, *readmes]


def unknown_effect_names(text: str, known: frozenset[str]) -> dict[str, list[int]]:
    """text が名指す、known に無い LLM の effect の名と、その行の番号(改名を言う行は読まない)。"""
    found: dict[str, list[int]] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if RENAME_LINE.search(line):
            continue
        for name in EFFECT_NAME.findall(line):
            if name not in known:
                found[name] = [*found.get(name, []), number]
    return found


# 名の拾い方の検が使う、doeff-llm の今の名の見本(doeff-llm を読まないので、どの環境でも走る)。
SAMPLE_KNOWN = frozenset({"LLMChat", "LLMStreamingChat", "LLMStructuredQuery", "LLMEmbedding"})


@pytest.fixture(scope="module")
def known() -> frozenset[str]:
    effects = pytest.importorskip(
        "doeff_llm.effects", reason="核の外の部品 doeff-llm がこの環境に入っていない"
    )
    return frozenset(effects.__all__)


def test_the_check_names_the_old_structured_output_effect() -> None:
    sample = "from doeff_llm.effects import LLMChat, LLMStructuredOutput\n- `LLMEmbed`\n- `LLMEmbedding`\n"
    assert unknown_effect_names(sample, SAMPLE_KNOWN) == {
        "LLMStructuredOutput": [1],
        "LLMEmbed": [2],
    }


def test_the_check_skips_the_line_that_says_the_name_was_renamed() -> None:
    sample = "- `LLMStructuredOutput` was renamed to `LLMStructuredQuery`.\n"
    assert unknown_effect_names(sample, SAMPLE_KNOWN) == {}


@pytest.mark.parametrize("page", _pages(), ids=lambda page: str(page.relative_to(REPO)))
def test_page_names_only_effects_that_doeff_llm_defines(page: Path, known: frozenset[str]) -> None:
    unknown = unknown_effect_names(page.read_text(encoding="utf-8"), known)
    assert unknown == {}, (
        f"{page.relative_to(REPO)} が名指す、doeff-llm に無い effect(名 → 行): {unknown}"
    )
