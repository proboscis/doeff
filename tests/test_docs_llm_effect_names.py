"""Test that the LLM effects named by docs and READMEs exist in today's doeff-llm (a bug found in initiative doeff-publish).

`LLMStructuredOutput` was renamed to `LLMStructuredQuery` (stated in docs/llm_unified_effects.md), and the old name does not exist in today's doeff-llm.
Even after the rename, the line in docs/20-why-effects-over-di.md that imports `LLMChat, LLMStructuredOutput`, and the lists of corresponding effects in the READMEs of the 3 providers
(doeff-openai, doeff-openrouter, doeff-gemini), kept the old name, and copying and running them gave ImportError.

This test collects every name of `LLMChat`, `LLMStreaming*`, `LLMStructured*`, `LLMEmbed*` that appears in docs/*.md and READMEs (root and each package's),
and checks that it is in `__all__` of `doeff_llm._effects`. Lines that state a rename (lines containing "renamed") and
the CHANGELOG are not read, because their job is to write the old name (the CHANGELOG is in neither docs/ nor a README, so it is not read).
doeff-llm is a component outside the core, so in an environment where it is not installed (the Python Compatibility venv of CI, which installs only the 5 core packages), the per-page tests that read doeff-llm
are skipped (importing at the top gave ModuleNotFoundError at collection and turned main's CI red with ERROR --
2026-10-17). The test of how names are picked does not read doeff-llm, so it runs in any environment.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
# The shape of doeff-llm effect names (does not include DI sample classes such as `LLMService`).
EFFECT_NAME = re.compile(r"\bLLM(?:Chat|Streaming\w*|Structured\w*|Embed\w*)\b")
RENAME_LINE = re.compile(r"renamed", re.IGNORECASE)


def _pages() -> list[Path]:
    docs = sorted((REPO / "docs").glob("*.md"))
    readmes = [REPO / "README.md", *sorted((REPO / "packages").glob("*/README.md"))]
    return [*docs, *readmes]


def unknown_effect_names(text: str, known: frozenset[str]) -> dict[str, list[int]]:
    """Names of LLM effects that text mentions and that are not in known, with their line numbers (lines stating a rename are not read)."""
    found: dict[str, list[int]] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if RENAME_LINE.search(line):
            continue
        for name in EFFECT_NAME.findall(line):
            if name not in known:
                found[name] = [*found.get(name, []), number]
    return found


# A sample of today's doeff-llm names used by the test of how names are picked (it does not read doeff-llm, so it runs in any environment).
SAMPLE_KNOWN = frozenset({"LLMChat", "LLMStreamingChat", "LLMStructuredQuery", "LLMEmbedding"})


@pytest.fixture(scope="module")
def known() -> frozenset[str]:
    effects = pytest.importorskip(
        "doeff_llm._effects", reason="doeff-llm, a component outside the core, is not installed in this environment"
    )
    return frozenset(effects.__all__)


def test_the_check_names_the_old_structured_output_effect() -> None:
    sample = "from doeff_llm._effects import LLMChat, LLMStructuredOutput\n- `LLMEmbed`\n- `LLMEmbedding`\n"
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
        f"effects named by {page.relative_to(REPO)} that are not in doeff-llm (name -> lines): {unknown}"
    )
