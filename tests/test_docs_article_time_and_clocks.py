"""Test that the examples in the third English article, "Time is an effect" (docs/articles/time-and-clocks.md), run as-is
on doeff and print as the article shows.

The fences are read the way the first two articles' tests read them:

- A fence starts with `~~~<language>`.
- If the first line of a python fence is `# <name>.py`, it is the whole of that file in examples/effects_tour. If it is
  `# <name>.py (excerpt)`, the chunks separated by blank lines appear in that file as-is and in that order. The article
  quotes only trading.py and test_trading.py. test_trading.py is also checked to pass under pytest in a dir laid out
  alongside trading.py.
- A python fence with no first line is an execution line: each line is a line of trading.py's `__main__` block.
- A text fence equals one segment of trading.py's output (the lines under a heading "<number>. ..."). Every segment is
  shown somewhere in the article, except the retry stack, which the article leaves out and names in "Try it".

The interpreter that runs the examples is the one running this test (the repository's doeff). Set DOEFF_ARTICLE_PYTHON to
the python of an environment with only doeff's wheels installed to run the same checks on those wheels (see
tests/test_docs_article_yield_and_async.py for the command).

The body's rules are checked too: no line claims an AI wrote it; the words that read as replacing I/O only (swap, fake,
mock, real I/O) are not in the prose (the axis is algebraic effects, not replacing I/O; code and inline code may name
`FakeExchange`); the article links to the first two posts; the layers come in the order of trading.py's stacks.
"""

from __future__ import annotations

import itertools
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from doeff_core_effects import ReadEnvironment, subprocess_handler

from doeff import run, with_handlers
from tests.cli.cli_child import with_settings

ROOT = Path(__file__).resolve().parents[1]
ARTICLE = ROOT / "docs" / "articles" / "time-and-clocks.md"
TOUR = ROOT / "examples" / "effects_tour"
# Example files the article may quote in fences (all from examples/effects_tour).
QUOTED_FILES = {"trading.py", "test_trading.py"}
# Files of the example dir to lay out when running the article's test_trading.py.
TRADING_FILES = ("trading.py", "pytest.ini")
# Segments of trading.py's output the article leaves out: 3 is the retry stack, which "Try it" names.
LEFT_OUT = {"3"}
# Per-child time limit, before scaling for load (seconds). trading.py takes about 2 seconds (mostly doeff's import).
CHILD_DEADLINE_SECONDS = 50

_FENCE = re.compile(r"^~~~(?P<language>\w*)\n(?P<body>.*?)^~~~$", re.MULTILINE | re.DOTALL)
_HEADER = re.compile(r"# (?P<name>\w+\.py)(?P<excerpt> \(excerpt\))?")
_SEGMENT = re.compile(r"^(\d+)\. .*$\n", re.MULTILINE)
_INLINE_CODE = re.compile(r"`[^`]*`")
# Words that read as replacing I/O only. In the prose they would make the article read as a story of swapping I/O.
_IO_ONLY_WORDS = re.compile(r"\b(swap\w*|fake\w*|mock\w*|real I/O)", re.IGNORECASE)
# Do not put in the article any line claiming that an AI wrote it (decision of 2026-10-22). Look only for the shapes of
# sentences that claim authorship.
_AI_AUTHORSHIP = re.compile(
    r"\b(written|drafted|generated|authored)\s+(by|with)\s+(an?\s+)?(AI|LLM|agent|Claude|ChatGPT)\b"
    r"|\b(an?\s+)?(AI|LLM)(\s+agent)?\s+(wrote|drafted|generated|authored)\b",
    re.IGNORECASE,
)


def _fences() -> list[tuple[str, str, int]]:
    """The article's `~~~` fences (language, contents, starting line)."""
    text = ARTICLE.read_text(encoding="utf-8")
    return [
        (found["language"], found["body"].rstrip("\n"), text.count("\n", 0, found.start()) + 1)
        for found in _FENCE.finditer(text)
    ]


def _header(body: str) -> re.Match[str] | None:
    """The first line of a python fence, `# <name>.py` or `# <name>.py (excerpt)` (None if absent)."""
    return _HEADER.fullmatch(body.splitlines()[0]) if body else None


def _source(name: str) -> str:
    """Contents of the effects_tour file named by the fence's first line."""
    return (TOUR / name).read_text(encoding="utf-8").rstrip("\n")


def _python() -> str:
    """The interpreter that runs the examples: the repository's doeff, or DOEFF_ARTICLE_PYTHON's environment."""
    entries = run(with_handlers([subprocess_handler], ReadEnvironment(("DOEFF_ARTICLE_PYTHON",))))
    return next((entry.value for entry in entries if entry.value), sys.executable)


def _child(command: list[str], cwd: Path, deadline_scale_factor: float, **settings: str | None):
    """Start a child interpreter in `cwd` like a reader would (read neither the repo's conftest nor pytest plugins)."""
    return subprocess.run(
        with_settings(command, {"DOEFF_PROFILE": None, **settings}),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
        check=False,
    )


def _segments(stdout: str) -> dict[str, str]:
    """Split trading.py's output into the lines under each heading "<number>. ..."."""
    parts = _SEGMENT.split(stdout)
    assert parts[0] == "", f"output before the first heading: {parts[0]!r}"
    return {parts[i]: parts[i + 1].rstrip("\n") for i in range(1, len(parts), 2)}


def _chunks(text: str) -> list[list[str]]:
    """Split an excerpt into units searched for in succession within the file (chunks separated by blank lines)."""
    return [
        [line for line in block.splitlines() if line.strip()]
        for block in re.split(r"\n\s*\n", text.strip("\n"))
        if block.strip()
    ]


def excerpt_problem(excerpt: str, source: str) -> str | None:
    """A sentence describing the chunk if it is not in the file as-is and in order (None if it is)."""
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
            return f"chunk missing from file or out of order: {chunk[0]!r}"
        start = at + len(chunk)
    return None


def prose_lines(text: str) -> list[tuple[int, str]]:
    """The article's lines outside `~~~` fences, with inline code removed (line number, text)."""
    lines = text.splitlines()
    # Whether each line is inside a fence: a `~~~` line opens or closes one, and counts as fenced itself.
    fences_before = list(
        itertools.accumulate((line.startswith("~~~") for line in lines), initial=0)
    )
    return [
        (number, _INLINE_CODE.sub("", line))
        for number, line in enumerate(lines, 1)
        if fences_before[number] % 2 == 0 and not line.startswith("~~~")
    ]


_NAMED = [
    (body, line) for language, body, line in _fences() if language == "python" and _header(body)
]


@pytest.mark.parametrize(("body", "line"), _NAMED, ids=[f"line{line}" for _, line in _NAMED])
def test_named_fences_are_the_example_files(body: str, line: int) -> None:
    header = _header(body)
    assert header is not None
    name = header["name"]
    source = _source(name)
    if header["excerpt"]:
        problem = excerpt_problem("\n".join(body.splitlines()[1:]), source)
        assert problem is None, f"article line {line}: {problem}"
    else:
        assert body == source, (
            f"article line {line}, {name} differs from the whole of the example file"
        )


def test_every_code_block_quotes_trading_or_its_test() -> None:
    named = {header["name"] for _, body, _ in _fences() if (header := _header(body))}
    assert named == QUOTED_FILES


def test_unnamed_python_fences_are_lines_the_example_runs() -> None:
    allowed = {
        line.strip()
        for line in _source("trading.py").split('\nif __name__ == "__main__":')[1].splitlines()
    }
    stray = [
        (line, text)
        for language, body, line in _fences()
        if language == "python" and not _header(body)
        for text in body.splitlines()
        if text.strip() and text.strip() not in allowed
    ]
    assert stray == []


def test_text_fences_are_what_the_example_prints(deadline_scale_factor: float) -> None:
    done = _child([_python(), "-W", "error", "trading.py"], TOUR, deadline_scale_factor)
    assert (done.returncode, done.stderr) == (0, ""), done.stderr[-3000:]
    segments = _segments(done.stdout.rstrip("\n"))
    texts = [(line, body) for language, body, line in _fences() if language == "text"]
    unmatched = [(line, body) for line, body in texts if body not in segments.values()]
    assert unmatched == [], (
        f"the article's output fences differ from every segment of trading.py: {unmatched}"
    )
    unshown = {key for key, segment in segments.items() if segment not in {b for _, b in texts}}
    assert unshown == LEFT_OUT, f"segments of trading.py the article does not show: {unshown}"


def test_the_article_test_passes_beside_the_example_file(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    for name in TRADING_FILES:
        shutil.copy(TOUR / name, tmp_path / name)
    (body,) = [body for _, body, _ in _fences() if body.startswith("# test_trading.py\n")]
    (tmp_path / "test_trading.py").write_text(body + "\n", encoding="utf-8")
    done = _child(
        [_python(), "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_trading.py"],
        tmp_path,
        deadline_scale_factor,
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTEST_ADDOPTS=None,
    )
    assert done.returncode == 0, f"{done.stdout[-3000:]}\n{done.stderr[-3000:]}"
    assert re.match(r"3 passed\b", done.stdout.splitlines()[-1]), done.stdout


_CLAIMS = """
from trading import *

try:
    run(sma_cross(5, 20))
    print("bare: ran")
except Exception as error:
    print("bare:", type(error).__name__, str(error).splitlines()[0])
# The strategy runs with handlers for Tick, Order and Log only: it yields no Delay and no GetTime
print("no clock:", run(quiet(recorded(BARS)(paper(0.0005)(sma_cross(5, 20))))))
"""


def test_the_claims_the_article_makes_about_the_strategy(deadline_scale_factor: float) -> None:
    done = _child([_python(), "-c", _CLAIMS], TOUR, deadline_scale_factor)
    assert done.returncode == 0, done.stderr[-3000:]
    bare, no_clock = done.stdout.splitlines()
    # Nothing in the strategy says where the bars come from: without handlers it stops at the first Tick
    assert bare.startswith("bare: UnhandledEffect"), bare
    assert "Tick" in bare, bare
    # "It does not yield Delay or GetTime"
    assert no_clock == "no clock: 50.79", no_clock


def test_the_layers_come_in_the_order_of_the_stacks() -> None:
    headings = re.findall(r"^### (.+)$", ARTICLE.read_text(encoding="utf-8"), re.MULTILINE)
    assert [h.split(":")[0] for h in headings] == [
        *(f"Layer {n}" for n in range(1, 7)),
        "Production",
        "The stacks side by side",
    ]


def test_the_excerpt_check_finds_a_shortened_excerpt() -> None:
    source = "a = 1\n\n\nb = 2\nc = 3\n\n\nd = 4"
    assert excerpt_problem("a = 1\n\nd = 4", source) is None
    assert excerpt_problem("b = 2", source) is None
    assert (
        excerpt_problem("d = 4\n\na = 1", source)
        == "chunk missing from file or out of order: 'a = 1'"
    )
    assert (
        excerpt_problem("b = 2\nd = 4", source)
        == "chunk missing from file or out of order: 'b = 2'"
    )


def test_the_article_does_not_say_an_ai_wrote_it() -> None:
    lines = ARTICLE.read_text(encoding="utf-8").splitlines()
    found = [f"{n}: {line}" for n, line in enumerate(lines, 1) if _AI_AUTHORSHIP.search(line)]
    assert not found, "the article claims an AI author:\n" + "\n".join(found)


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


def test_io_only_words_stay_out_of_the_prose() -> None:
    found = [
        f"{number}: {line}"
        for number, line in prose_lines(ARTICLE.read_text(encoding="utf-8"))
        if _IO_ONLY_WORDS.search(line)
    ]
    assert not found, "words that read as replacing I/O only, in the prose:\n" + "\n".join(found)


def test_the_io_word_check_reads_prose_only() -> None:
    text = "Swap the clock.\n`FakeExchange` stands in.\n~~~python\nclass FakeExchange:\n~~~\na mock clock\n"
    assert [line for _, line in prose_lines(text) if _IO_ONLY_WORDS.search(line)] == [
        "Swap the clock.",
        "a mock clock",
    ]


def test_the_article_links_to_the_first_two_posts() -> None:
    text = ARTICLE.read_text(encoding="utf-8")
    assert "](algebraic-effects-in-python.md)" in text
    assert "](yield-and-async.md)" in text
