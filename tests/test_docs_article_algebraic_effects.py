"""Test that the examples in the first English article, "Algebraic effects for Python" (docs/articles/algebraic-effects-in-python.md), run as-is on
the current doeff alone (without installing any subpackage) and print as the article shows (initiative doeff-publish.
The examples are produced from doeff core only).

The article stays as the version that passed review (the body is not rewritten to fit the test's conventions). So the fences are read not by the README convention
(tools/readme_examples.py) but as the article writes them:

- A fence starts with `~~~<language>` (``` fences are mermaid diagrams).
- If the first line of a python fence is `# <name>.py`, it is the whole of that file in examples/effects_tour. If it is `# <name>.py (excerpt)`, the chunks separated by blank lines
  appear in that file as-is and in that order. The only files the article quotes are spend.py, spire.py, and test_spire.py.
  test_spire.py is also checked to pass under pytest in a dir laid out alongside spire.py.
- A python fence with no first line is an execution line: the line is a statement of spire.py's tour() or play() (also accepted with the leading `"  ->"` of the output removed), or
  a line that forgot one handler.
- A text fence equals one of: spend.py's output, one segment of spire.py's output (under the heading "<number>. ..."; strip the leading `  -> ` of lines), or the head of the doeff
  traceback when a handler is forgotten.

The article is written entirely with the card-game battle example (spire.py) (no sentences of the monitoring example monitor.py are left in the article). Claims in the body that can be checked by running are also covered: without a handler the first Roll gives
UnhandledEffect; a fight with the same seed is always the same; a run inside the Monte Carlo handler does not use the outer dice.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.cli.cli_child import with_settings

ROOT = Path(__file__).resolve().parents[1]
ARTICLE = ROOT / "docs" / "articles" / "algebraic-effects-in-python.md"
TOUR = ROOT / "examples" / "effects_tour"
# Per-child time limit, before scaling for load (seconds). One takes about 1 second (mostly doeff's import).
CHILD_DEADLINE_SECONDS = 50
# spire.py's tour takes about 25 seconds (mostly the 100 Monte Carlo fights). The convention at the head of spire.py is within 60 seconds.
SPIRE_DEADLINE_SECONDS = 120

_FENCE = re.compile(r"^~~~(?P<language>\w*)\n(?P<body>.*?)^~~~$", re.MULTILINE | re.DOTALL)
_HEADER = re.compile(r"# (?P<name>\w+\.py)(?P<excerpt> \(excerpt\))?")
_SEGMENT = re.compile(r"^(\d+)\. .*$\n", re.MULTILINE)
# Marker at the head of tour()'s output. The article shows the lines and output with the marker removed.
ARROW = "  -> "
# The article's first-half range, and monitoring-example words that must not remain anywhere in the article.
FRONT = ("## The idea", "## One mechanism under all of it")
MONITOR_WORDS = re.compile(r"two pages|half(?:-| )an? hour|half hour|\bmonitor", re.IGNORECASE)
# The line in the article's "Tests are one of the stacks" that forgot the Played handler (moves is from the Layer 1 line).
FORGOT = (
    "from doeff import run\n"
    "from spire import *\n"
    "moves = [0, 0, 0, None] * 20\n"
    "run(silent(dice(7)(scripted(moves)(battle()))))\n"
)
# Example files the article may quote in fences (all from examples/effects_tour).
QUOTED_FILES = {"spend.py", "spire.py", "test_spire.py"}
# Files of the example dir to lay out when running the article's test_spire.py.
SPIRE_FILES = ("spire.py", "pytest.ini")


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


def _child(
    command: list[str],
    cwd: Path,
    deadline_scale_factor: float,
    deadline_seconds: float = CHILD_DEADLINE_SECONDS,
    **settings: str | None,
):
    """Start a child interpreter in the example dir like a reader would (read neither the repo's conftest nor pytest plugins)."""
    return subprocess.run(
        with_settings(command, {"DOEFF_PROFILE": None, **settings}),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=deadline_seconds * deadline_scale_factor,
        check=False,
    )


def _printed(
    name: str, deadline_scale_factor: float, deadline_seconds: float = CHILD_DEADLINE_SECONDS
) -> str:
    """Output of running an effects_tour file with `python <name>`."""
    done = _child(
        [sys.executable, "-W", "error", name], TOUR, deadline_scale_factor, deadline_seconds
    )
    assert (done.returncode, done.stderr) == (0, ""), done.stderr[-3000:]
    return done.stdout.rstrip("\n")


def _unarrowed(text: str) -> str:
    """Strip the leading `  -> ` from tour()'s output lines."""
    return "\n".join(line.removeprefix(ARROW) for line in text.splitlines())


def _segments(stdout: str) -> dict[str, str]:
    """Split spire.py's output into lines per heading "<number>. ..." (with the leading `  -> ` removed)."""
    parts = _SEGMENT.split(stdout)
    return {
        f"spire.py:{parts[i]}": _unarrowed(parts[i + 1].rstrip("\n"))
        for i in range(1, len(parts), 2)
    }


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


def _main_lines() -> set[str]:
    """Lines allowed in a python fence with no first line (statements of spire.py's tour() and play(), the same with the leading output marker removed, and the forgot line)."""
    source = _source("spire.py")
    functions = [
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name in {"tour", "play"}
    ]
    statements = {
        ast.get_source_segment(source, statement)
        for function in functions
        for statement in function.body
        if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Constant)
    }
    unarrowed = {
        text.replace('print("  ->", ', "print(").replace('print("  -> ', 'print("')
        for text in statements
        if text
    }
    return {text for text in statements | unarrowed if text} | {FORGOT.splitlines()[-1]}


def _spire_dir(directory: Path) -> Path:
    """Make in `directory` a dir with the article's test_spire.py alongside the example dir's spire.py."""
    for name in SPIRE_FILES:
        shutil.copy(TOUR / name, directory / name)
    (body,) = [body for _, body, _ in _fences() if body.startswith("# test_spire.py\n")]
    (directory / "test_spire.py").write_text(body + "\n", encoding="utf-8")
    return directory


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
        assert body == source, f"article line {line}, {name} differs from the whole of the example file"


def test_unnamed_python_fences_are_lines_the_examples_run() -> None:
    allowed = _main_lines()
    stray = [
        (line, text)
        for language, body, line in _fences()
        if language == "python" and not _header(body)
        for text in body.splitlines()
        if text.strip() and text.strip() not in allowed
    ]
    assert stray == []


def test_every_code_block_quotes_the_battle_or_spend() -> None:
    named = {header["name"] for _, body, _ in _fences() if (header := _header(body))}
    assert named == QUOTED_FILES


def test_text_fences_are_what_the_examples_print(deadline_scale_factor: float) -> None:
    forgot = _child([sys.executable, "-c", FORGOT], TOUR, deadline_scale_factor).stderr
    printed = {
        "spend.py": _printed("spend.py", deadline_scale_factor),
        # When forgotten, the article shows only the head that doeff prints before Python's traceback
        "forgot": forgot.strip("\n").split("\nTraceback (most recent call last):")[0].strip("\n"),
        **_segments(_printed("spire.py", deadline_scale_factor, SPIRE_DEADLINE_SECONDS)),
    }
    unmatched = [
        (line, body)
        for language, body, line in _fences()
        if language == "text" and body not in printed.values()
    ]
    assert unmatched == [], f"the article's output fences differ from every example's output: {unmatched}"


def test_every_output_the_article_quotes_is_shown() -> None:
    # The 6 segments of spire.py's output (Layer 1-6), plus spend.py and the traceback when forgotten. If a fence disappears, the tests above silently decrease.
    texts = [body for language, body, _ in _fences() if language == "text"]
    assert len(texts) == 8


def _front() -> str:
    """The first half of the article (The idea to The stacks side by side)."""
    text = ARTICLE.read_text(encoding="utf-8")
    return text[text.index(FRONT[0]) : text.index(FRONT[1])]


def test_the_article_has_no_monitor_words() -> None:
    lines = ARTICLE.read_text(encoding="utf-8").splitlines()
    found = [f"{n}: {line}" for n, line in enumerate(lines, 1) if MONITOR_WORDS.search(line)]
    assert found == [], f"monitoring-example sentences remain in the article: {found}"


def test_the_front_half_is_the_battle_and_not_the_monitor() -> None:
    front = _front()
    # Layer 1-6 and the production section line up in spire.py's stack order
    headings = re.findall(r"^### (.+)$", front, re.MULTILINE)
    assert [h.split(":")[0] for h in headings] == [
        *(f"Layer {n}" for n in range(1, 7)),
        "Production",
        "The stacks side by side",
    ]
    # Excerpts of example files come from spire.py (monitor.py is not quoted in the first half)
    named = {header["name"] for _, body, _ in _fences() if (header := _header(body))}
    assert "spire.py" in named
    assert "monitor.py" not in named


@pytest.mark.parametrize(
    "line",
    [
        "`monitor` checks two pages every half hour, one task per page.",
        "This is the same `monitor` on the wall clock, so this run really takes half an hour.",
    ],
)
def test_the_monitor_words_check_finds_the_old_lines(line: str) -> None:
    assert MONITOR_WORDS.search(line)


def test_the_article_test_passes_beside_the_example_files(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    spire_dir = _spire_dir(tmp_path)
    done = _child(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_spire.py"],
        spire_dir,
        deadline_scale_factor,
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTEST_ADDOPTS=None,
    )
    assert done.returncode == 0, f"{done.stdout[-3000:]}\n{done.stderr[-3000:]}"
    assert re.match(r"2 passed\b", done.stdout.splitlines()[-1]), done.stdout


_CLAIMS = """
from doeff import Pass, handler
from spire import *

try:
    run(battle())
    print("bare: ran")
except Exception as error:
    print("bare:", type(error).__name__, str(error).splitlines()[0])
moves = [0, 0, 0, None] * 20
runs = {run(quiet_stats(silent(dice(7)(scripted(moves)(battle()))))) for _ in range(3)}
print("same seed:", len(runs))
# A run inside the Monte Carlo handler answers with its own dice: the outer dice's sequence advances in the same shape as with the random bot
outer = []

def counted(seed):
    inner = dice(seed)

    @handler
    def count(effect: Roll, k):
        outer.append(effect.n)
        return (yield Pass(effect, k))

    return lambda program: inner(count(program))

run(quiet_stats(silent(counted(3)(monte_carlo(PLAYOUTS)(battle())))))
print("outer rolls:", all(n <= 10 for n in outer), len(outer) < 100)
"""


def test_the_claims_the_article_makes_about_battle(deadline_scale_factor: float) -> None:
    done = _child([sys.executable, "-c", _CLAIMS], TOUR, deadline_scale_factor)
    assert done.returncode == 0, done.stderr[-3000:]
    bare, same, outer = done.stdout.splitlines()
    # Without a handler, it stops at the first Roll (body: "none of these effects has a meaning until a handler gives it one")
    assert bare.startswith("bare: UnhandledEffect"), bare
    assert "Roll" in bare, bare
    # A fight with the same seed and the same script is always the same (body: "the same fight on every run")
    assert same == "same seed: 1", same
    # The playout's run Roll does not reach the outer dice (body: "they do not use up the dice of the paused fight")
    assert outer == "outer rolls: True True", outer


def test_the_excerpt_check_finds_a_shortened_excerpt() -> None:
    source = "a = 1\n\n\nb = 2\nc = 3\n\n\nd = 4"
    assert excerpt_problem("a = 1\n\nd = 4", source) is None
    assert excerpt_problem("b = 2", source) is None
    assert excerpt_problem("d = 4\n\na = 1", source) == "chunk missing from file or out of order: 'a = 1'"
    assert excerpt_problem("b = 2\nd = 4", source) == "chunk missing from file or out of order: 'b = 2'"


# Do not put in the article any line claiming that an AI wrote it or an AI agent wrote it (decision of 2026-10-22:
# do not put a disclaimer in the article saying an AI wrote it). Look only for the shapes of sentences that claim authorship.
_AI_AUTHORSHIP = re.compile(
    r"\b(written|drafted|generated|authored)\s+(by|with)\s+(an?\s+)?(AI|LLM|agent|Claude|ChatGPT)\b"
    r"|\b(an?\s+)?(AI|LLM)(\s+agent)?\s+(wrote|drafted|generated|authored)\b",
    re.IGNORECASE,
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
