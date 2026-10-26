"""Fail if a text of the first week says that an AI, an LLM or an agent wrote it.

The maintainer forbade such lines in the article (2026-10-22) and on X (2026-10-20). This looks
only for lines that name an AI as the writer, in English or Japanese; a line about the library's
LLM packages, or about commits made with Claude Code, is not a byline and passes.

Usage: uv run --no-project python publications/launch-week-1/check_no_ai_byline.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

PIECES = [
    ROOT / "docs/articles/algebraic-effects-in-python.md",
    ROOT / "publications/zenn-use-cases-v0/doeff-main.md",
    HERE / "pycoders.md",
    HERE / "rpython.md",
    HERE / "x/en-1.txt",
    HERE / "x/en-2.txt",
    HERE / "x/ja-1.txt",
    HERE / "x/ja-2.txt",
    HERE / "x/narration.json",
    HERE / "show-hn-facts.md",
]

AI = r"(AI|LLM|agent|Claude|ChatGPT|GPT)"
BYLINE = re.compile(
    rf"\b(written|drafted|generated|authored)\s+(by|with)\s+(an?\s+)?{AI}\b"
    rf"|\b(an?\s+)?(AI|LLM)(\s+agent)?\s+(wrote|drafted|generated|authored)\b"
    rf"|{AI}(エージェント)?(が|で|によって|により)[^。|]{{0,120}}(書きました|書き、|書いた。|書いています|執筆|生成しました)",
    re.IGNORECASE,
)

# The lines that were taken out; the check must still find each of them.
REMOVED = [
    "*Written by an AI agent that works on doeff for its maintainer, [@proboscis](https://github.com/proboscis).*",
    "*An AI agent wrote this post, and every example was run with doeff 0.8.0 from PyPI and doeff-time.*",
    "*Written by an AI agent that works on doeff for its maintainer ([proboscis](https://github.com/proboscis) on GitHub).*",
    "*AIエージェントが、メンテナの[@proboscis](https://github.com/proboscis)のもとでdoeffの開発を手伝いながら書きました。*",
    "*本文はAIエージェントが書き、すべての例をPyPIのdoeff 0.8.0とdoeff-timeで走らせました。*",
]


def main() -> int:
    missed = [line for line in REMOVED if not BYLINE.search(line)]
    if missed:
        print("the check does not find a removed line:", *missed, sep="\n")
        return 1
    found = [
        f"{path.relative_to(ROOT)}:{number}: {line}"
        for path in PIECES
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if BYLINE.search(line)
    ]
    if found:
        print("a piece says an AI wrote it:", *found, sep="\n")
        return 1
    print(f"OK: {len(PIECES)} files, no AI byline; the {len(REMOVED)} removed lines are still caught")
    return 0


if __name__ == "__main__":
    sys.exit(main())
