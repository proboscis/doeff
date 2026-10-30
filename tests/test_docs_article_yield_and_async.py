"""英語の記事 2 本目「Yield and async」(docs/articles/yield-and-async.md)の例が doeff でそのまま走り、記事が見せる通りに
印字する事の検(取り組み doeff-publish・card ki-719ec9da0a01)。

囲みは 1 本目の記事(tests/test_docs_article_algebraic_effects.py)と同じ書き方で読む:

- 囲みは `~~~<言葉>` で始まる(``` の囲みは mermaid の図)。
- python の囲みの頭の行が `# <名>.py` なら、examples/yield_and_async のその file の全部。`# <名>.py (excerpt)` なら、空行で
  区切った塊がその file の中にそのままその順に在る。
- 頭の行の無い python の囲みは実行の行: 行はどれかの例の file の `__main__` の行。
- text の囲みは、例の file の印字の 1 区切り(見出し「-- <鍵>: ...」の下)のどれかと同じ。逆に、どの区切りも記事のどこかの
  text の囲みが見せる(区切りを足して記事に書き忘れた時も赤にする)。

例を走らせる interpreter は、既定で検を走らせている物(repo の doeff)。環境変数 DOEFF_ARTICLE_PYTHON に、doeff の wheel だけを
入れた環境の python を渡すと、同じ検をその wheel で走らせる(記事は「doeff だけでそのまま走る」と書く)。記事は await_handler を
() の要らない形で書くので、0.8.0 の wheel では走らない — main を組んだ wheel(doeff・doeff-core-effects・doeff-vm)か次の版で
走らせる。--with の環境は命令が終わると消えるので、pytest をその中で走らせる:

    uv run --no-project --with <doeff の wheel> --with <doeff-core-effects の wheel> --with <doeff-vm の wheel> \\
        bash -c 'DOEFF_ARTICLE_PYTHON="$(python -c "import sys; print(sys.executable)")" \\
            env -u VIRTUAL_ENV uv run pytest tests/test_docs_article_yield_and_async.py'

本文の決まりも見る: AI が書いたと名乗る行が無い・IO の差し替えだけに読める語(swap・fake・mock・real I/O)が見出し「## I/O」の
節の外に無い(軸は algebraic effects — 記事を IO の差し替えの話に縮めない)・1 本目の記事と互いに link し合う。
"""

from __future__ import annotations

import itertools
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.cli.cli_child import with_settings

ROOT = Path(__file__).resolve().parents[1]
ARTICLE = ROOT / "docs" / "articles" / "yield-and-async.md"
FIRST_ARTICLE = ROOT / "docs" / "articles" / "algebraic-effects-in-python.md"
EXAMPLES = ROOT / "examples" / "yield_and_async"
EXAMPLE_FILES = ("greet.py", "drive.py", "stream.py")
# 子 1 つの制限時間の、負荷で伸ばす前の値(秒)。1 本は約 1 秒(多くは doeff の import)。
CHILD_DEADLINE_SECONDS = 50

_FENCE = re.compile(r"^~~~(?P<language>\w*)\n(?P<body>.*?)^~~~$", re.MULTILINE | re.DOTALL)
_HEADER = re.compile(r"# (?P<name>\w+\.py)(?P<excerpt> \(excerpt\))?")
_SEGMENT = re.compile(r"^-- ([\w ]+): .*$\n", re.MULTILINE)
# IO の差し替えだけに読める語。記事が IO の例の節(見出しが「## I/O」で始まる節)の外で使えば、IO の差し替えの話に読める。
_IO_ONLY_WORDS = re.compile(r"\b(swap\w*|fake\w*|mock\w*|real I/O)", re.IGNORECASE)
# 記事の中に、AI が書いた・AI の agent が書いたと名乗る行を置かない(持ち主の決定 2026-10-22)。書き手を名乗る文の形だけを探す。
_AI_AUTHORSHIP = re.compile(
    r"\b(written|drafted|generated|authored)\s+(by|with)\s+(an?\s+)?(AI|LLM|agent|Claude|ChatGPT)\b"
    r"|\b(an?\s+)?(AI|LLM)(\s+agent)?\s+(wrote|drafted|generated|authored)\b",
    re.IGNORECASE,
)


def _fences() -> list[tuple[str, str, int]]:
    """記事の `~~~` の囲み(言葉・中身・始まりの行)— 検はどれも記事の囲みを例の file と比べるため。"""
    text = ARTICLE.read_text(encoding="utf-8")
    return [
        (found["language"], found["body"].rstrip("\n"), text.count("\n", 0, found.start()) + 1)
        for found in _FENCE.finditer(text)
    ]


def _header(body: str) -> re.Match[str] | None:
    """python の囲みが、どの例の file の全部か抜き書きかを名指す頭の行(無ければ None — 実行の行の囲み)。"""
    return _HEADER.fullmatch(body.splitlines()[0]) if body else None


def _source(name: str) -> str:
    """囲みの頭の行が名指す例の file の中身 — 囲みと比べる元。"""
    return (EXAMPLES / name).read_text(encoding="utf-8").rstrip("\n")


def _python() -> str:
    """例を走らせる interpreter — 既定は repo の doeff、DOEFF_ARTICLE_PYTHON で PyPI の doeff の環境を指せるようにするため。"""
    return os.environ.get("DOEFF_ARTICLE_PYTHON") or sys.executable


def _printed(name: str, deadline_scale_factor: float) -> str:
    """読み手と同じく例の dir で `python <名>` を走らせた印字(repo の conftest も pytest の plugin も読ませない)。"""
    done = subprocess.run(
        with_settings([_python(), "-W", "error", name], {"DOEFF_PROFILE": None}),
        cwd=EXAMPLES,
        capture_output=True,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
        check=False,
    )
    assert (done.returncode, done.stderr) == (0, ""), done.stderr[-3000:]
    return done.stdout.rstrip("\n")


def _segments(stdout: str) -> list[str]:
    """例の印字を、見出し「-- <鍵>: ...」ごとの区切りに分ける — 記事の text の囲みは 1 区切りずつ見せるため。"""
    parts = _SEGMENT.split(stdout)
    assert parts[0] == "", f"見出しの前に印字が在る: {parts[0]!r}"
    return [parts[i + 1].rstrip("\n") for i in range(1, len(parts), 2)]


def _chunks(text: str) -> list[list[str]]:
    """抜き書きを、file の中で続いて探す単位(空行で区切った塊)に分ける — 抜き書きは塊を飛ばしてよいため。"""
    return [
        [line for line in block.splitlines() if line.strip()]
        for block in re.split(r"\n\s*\n", text.strip("\n"))
        if block.strip()
    ]


def excerpt_problem(excerpt: str, source: str) -> str | None:
    """抜き書きの塊が、file の中にそのままその順に無ければ、その塊を言う文(在れば None)— 記事の抜き書きが file からずれたら知るため。"""
    lines = source.splitlines()
    start = 0
    for chunk in _chunks(excerpt):
        at = next(
            (
                i
                for i in range(start, len(lines) - len(chunk) + 1)
                if lines[i : i + len(chunk)] == chunk
            ),
            None,
        )
        if at is None:
            return f"塊が file に無いか順が違う: {chunk[0]!r}"
        start = at + len(chunk)
    return None


def io_words_outside_the_io_section(text: str) -> list[str]:
    """見出しが「## I/O」で始まる節の外に在る、IO の差し替えだけに読める語の行 — 記事の軸を algebraic effects に保つため。"""
    lines = text.splitlines()
    # 行ごとの、その行が属する節の見出し(最初の「## 」より前は "")
    headings = itertools.accumulate(
        lines, lambda heading, line: line if line.startswith("## ") else heading, initial=""
    )
    return [
        f"{number}: {word[0]}: {line}"
        for number, (line, heading) in enumerate(zip(lines, list(headings)[1:], strict=True), 1)
        if not heading.startswith("## I/O") and (word := _IO_ONLY_WORDS.search(line))
    ]


_NAMED = [
    (body, line) for language, body, line in _fences() if language == "python" and _header(body)
]


@pytest.mark.parametrize(("body", "line"), _NAMED, ids=[f"line{line}" for _, line in _NAMED])
def test_named_fences_are_the_example_files(body: str, line: int) -> None:
    header = _header(body)
    assert header is not None
    name = header["name"]
    assert name in EXAMPLE_FILES, f"記事の {line} 行目が、例の dir に無い file {name} を名指す"
    source = _source(name)
    if header["excerpt"]:
        problem = excerpt_problem("\n".join(body.splitlines()[1:]), source)
        assert problem is None, f"記事の {line} 行目: {problem}"
    else:
        assert body == source, f"記事の {line} 行目の {name} が、例の file の全部と違う"


def test_unnamed_python_fences_are_lines_the_examples_run() -> None:
    allowed = {
        line.strip()
        for name in EXAMPLE_FILES
        for line in _source(name).split('\nif __name__ == "__main__":')[1].splitlines()
    }
    stray = [
        (line, text)
        for language, body, line in _fences()
        if language == "python" and not _header(body)
        for text in body.splitlines()
        if text.strip() and text.strip() not in allowed
    ]
    assert stray == []


def test_text_fences_are_what_the_examples_print(deadline_scale_factor: float) -> None:
    segments = {
        f"{name}#{index}": segment
        for name in EXAMPLE_FILES
        for index, segment in enumerate(_segments(_printed(name, deadline_scale_factor)))
    }
    texts = [(line, body) for language, body, line in _fences() if language == "text"]
    unmatched = [(line, body) for line, body in texts if body not in segments.values()]
    assert unmatched == [], f"記事の出力の囲みが、どの例の印字とも違う: {unmatched}"
    unshown = [key for key, segment in segments.items() if segment not in {b for _, b in texts}]
    assert unshown == [], f"例の印字の区切りのうち、記事が見せない物: {unshown}"


def test_the_excerpt_check_finds_a_shortened_excerpt() -> None:
    source = "a = 1\n\n\nb = 2\nc = 3\n\n\nd = 4"
    assert excerpt_problem("a = 1\n\nd = 4", source) is None
    assert excerpt_problem("b = 2", source) is None
    assert excerpt_problem("d = 4\n\na = 1", source) == "塊が file に無いか順が違う: 'a = 1'"
    assert excerpt_problem("b = 2\nd = 4", source) == "塊が file に無いか順が違う: 'b = 2'"


def test_the_article_does_not_say_an_ai_wrote_it() -> None:
    lines = ARTICLE.read_text(encoding="utf-8").splitlines()
    found = [f"{n}: {line}" for n, line in enumerate(lines, 1) if _AI_AUTHORSHIP.search(line)]
    assert not found, "記事が AI の書き手を名乗っている:\n" + "\n".join(found)


@pytest.mark.parametrize(
    "line",
    [
        "*Written by an AI agent that works on doeff for its maintainer.*",
        "*An AI agent wrote this post, and every example was run with doeff.*",
        "This post was generated with Claude.",
    ],
)
def test_the_ai_authorship_check_finds_the_lines_it_forbids(line: str) -> None:
    assert _AI_AUTHORSHIP.search(line)


def test_io_only_words_stay_in_the_io_section() -> None:
    found = io_words_outside_the_io_section(ARTICLE.read_text(encoding="utf-8"))
    assert not found, "IO の差し替えだけに読める語が、IO の例の節の外に在る:\n" + "\n".join(found)


def test_the_io_word_check_reads_sections() -> None:
    text = (
        "# Title\nSwap the client.\n## Where a yield goes\na fake clock\n"
        "## I/O through an async library\nno real I/O, no mocks\n## Summary\nmock.patch\n"
    )
    assert [entry.split(": ")[1] for entry in io_words_outside_the_io_section(text)] == [
        "Swap",
        "fake",
        "mock",
    ]


def test_the_first_and_second_posts_link_to_each_other() -> None:
    assert "](algebraic-effects-in-python.md)" in ARTICLE.read_text(encoding="utf-8")
    assert "](yield-and-async.md)" in FIRST_ARTICLE.read_text(encoding="utf-8")
