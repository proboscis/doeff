"""Test that the examples in the fourth English article, "Record and replay" (docs/articles/record-and-replay.md), run as-is
on doeff alone (no subpackage, no extra) and print as the article shows.

The examples define HttpRequest as their own effect, keep the answers with a JSON handler that answers the 3 storage effects
of make_memo_rewriter, and send with a handler that awaits urllib through Await. doeff-core-effects' HttpRequest,
http_production_handler and sqlite_memo_handler are written in Hy and need the http and hy extras, so the examples do not
use them.

The fences are read the way the first three articles' tests read them:

- A fence starts with `~~~<language>` (``` fences are mermaid diagrams).
- If the first line of a python fence is `# <name>.py`, it is the whole of that file in examples/record_and_replay. If it is
  `# <name>.py (excerpt)`, the chunks separated by blank lines appear in that file as-is and in that order.
- A python fence with no first line is an execution line: each line is a line of some example file's `__main__` block.
- A text fence equals one segment of an example file's output (the lines under a heading "-- <key>: ..."). Every segment
  is shown somewhere in the article (adding a segment and forgetting to show it fails too).

The interpreter that runs the examples is the one running this test (the repository's doeff). Set DOEFF_ARTICLE_PYTHON to
the python of an environment with only doeff's wheels installed to run the same checks on those wheels (see
tests/test_docs_article_yield_and_async.py for the command).

The claims of the body that running can check are checked too: no handler of HttpRequest sits outside the replay; an
HttpRequest has no hash, and two that differ only in the token are not equal; the order of the effects the memo layer
yields; answering the same 3 effects from a dict replays from memory the same way; with no storage handler every request
goes out; a POST is recorded and not sent on replay; the token is not in the file; the default key changes with the pickle
protocol; another process replays from the same file.

The body's rules are checked too: no line claims an AI wrote it; the words that read as replacing I/O only (swap, fake,
mock, real I/O) are not outside the section whose heading starts with "## I/O" (the axis is algebraic effects, not
replacing I/O); the article links to the first three posts.
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
ARTICLE = ROOT / "docs" / "articles" / "record-and-replay.md"
EXAMPLES = ROOT / "examples" / "record_and_replay"
EXAMPLE_FILES = ("releases.py", "on_disk.py", "live.py")
# Per-child time limit, before scaling for load (seconds). One child takes about 1 second (mostly doeff's import).
CHILD_DEADLINE_SECONDS = 50
# Warnings in the child are errors (an unclosed resource's ResourceWarning fails too).
WARNINGS = ["-W", "error"]

_FENCE = re.compile(r"^~~~(?P<language>\w*)\n(?P<body>.*?)^~~~$", re.MULTILINE | re.DOTALL)
_HEADER = re.compile(r"# (?P<name>\w+\.py)(?P<excerpt> \(excerpt\))?")
_SEGMENT = re.compile(r"^-- ([\w ]+): .*$\n", re.MULTILINE)
# Words that read as replacing I/O only. Outside the I/O example's section (heading starting with "## I/O") they would
# make the article read as a story of swapping I/O.
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
    """Contents of the example file named by the fence's first line."""
    return (EXAMPLES / name).read_text(encoding="utf-8").rstrip("\n")


def _python() -> str:
    """The interpreter that runs the examples: the repository's doeff, or DOEFF_ARTICLE_PYTHON's environment."""
    return os.environ.get("DOEFF_ARTICLE_PYTHON") or sys.executable


def _child(arguments: list[str], deadline_scale_factor: float) -> str:
    """The output of a child interpreter started in the example dir like a reader would (no conftest, no pytest plugin)."""
    done = subprocess.run(
        with_settings([_python(), *WARNINGS, *arguments], {"DOEFF_PROFILE": None}),
        cwd=EXAMPLES,
        capture_output=True,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
        check=False,
    )
    assert (done.returncode, done.stderr) == (0, ""), done.stderr[-3000:]
    return done.stdout.rstrip("\n")


def _segments(stdout: str) -> list[str]:
    """Split an example's output into the lines under each heading "-- <key>: ..."."""
    parts = _SEGMENT.split(stdout)
    assert parts[0] == "", f"output before the first heading: {parts[0]!r}"
    return [parts[i + 1].rstrip("\n") for i in range(1, len(parts), 2)]


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


def io_words_outside_the_io_section(text: str) -> list[str]:
    """Lines outside the section whose heading starts with "## I/O" that hold a word reading as replacing I/O only."""
    lines = text.splitlines()
    # The heading of the section each line belongs to ("" before the first "## ").
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
    assert name in EXAMPLE_FILES, f"article line {line} names {name}, which is not in the example dir"
    source = _source(name)
    if header["excerpt"]:
        problem = excerpt_problem("\n".join(body.splitlines()[1:]), source)
        assert problem is None, f"article line {line}: {problem}"
    else:
        assert body == source, (
            f"article line {line}, {name} differs from the whole of the example file"
        )


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
        for index, segment in enumerate(_segments(_child([name], deadline_scale_factor)))
    }
    texts = [(line, body) for language, body, line in _fences() if language == "text"]
    unmatched = [(line, body) for line, body in texts if body not in segments.values()]
    assert unmatched == [], (
        f"the article's output fences differ from every segment of the examples: {unmatched}"
    )
    unshown = [key for key, segment in segments.items() if segment not in {b for _, b in texts}]
    assert unshown == [], f"segments of the examples' output the article does not show: {unshown}"


# Prints the body's claims one by one with the example files' parts (one claim per line, "<name>: <value>"; lines that
# share a name are compared in the order they come).
_CLAIMS = """
import contextlib, io, tempfile
from pathlib import Path

from doeff import Pass, Resume, do, handler, run
from doeff_core_effects import (
    MemoExistsEffect, MemoGetEffect, MemoPutEffect, await_handler, make_memo_rewriter, scheduled,
    slog_discard_handler,
)

from on_disk import json_tape, method_and_url, offline, recorded
from releases import REPOS, HttpRequest, github, latest_releases, sent

def stacked(program, *handlers):
    for install in handlers:
        program = install(program)
    return run(scheduled(await_handler(slog_discard_handler(program))))

# No handler of HttpRequest sits outside the replay (body: "had replay passed a request on, ... UnhandledEffect").
# The doeff traceback run writes to stderr is not read here.
try:
    with contextlib.redirect_stderr(io.StringIO()):
        run(latest_releases(REPOS, "secret"))
    print("bare: ran")
except Exception as error:
    print("bare:", type(error).__name__, "HttpRequest" in str(error))

# The request itself cannot key the tape: its headers are a dict, so it has no hash, and two that differ only in the
# token are different requests (body: "The request holds a dict, its headers, so it has no hash ...").
url = "https://api.github.com/repos/acme/server/releases/latest"
try:
    hash(HttpRequest("GET", url))
    print("hash: ok")
except TypeError:
    print("hash: TypeError")
print("equal:", HttpRequest("GET", url, {"Authorization": "Bearer a"}) == HttpRequest("GET", url, {"Authorization": "Bearer b"}))

# The order of the effects the memo layer yields toward the storage handler (first row of the body's table; Slog left
# out). Prints each effect that passes.
def watching(name):
    @handler
    def watch(effect, k):
        if type(effect).__name__ != "SlogEffect":
            print(f"{name}:", type(effect).__name__)
        return (yield Pass(effect, k))
    return watch

directory = Path(tempfile.mkdtemp())
for name, network in (("miss", github), ("hit", offline)):
    stacked(latest_releases(["acme/server"], "secret"), make_memo_rewriter(HttpRequest),
            watching(name), json_tape(directory / "watched.json"), network)

# Answer the same 3 from a dict, and the same layer replays from memory (body: "Answer the same three effects from a
# dict instead ...").
def dict_tape(tape):
    @handler
    def storage(effect: MemoExistsEffect | MemoGetEffect | MemoPutEffect, k):
        if isinstance(effect, MemoExistsEffect):
            return (yield Resume(k, effect.key in tape))
        if isinstance(effect, MemoGetEffect):
            return (yield Resume(k, tape[effect.key]))
        tape[effect.key] = effect.value
        return (yield Resume(k, None))
    return storage

memory = {}
sent.clear()
first = stacked(latest_releases(REPOS, "secret"), make_memo_rewriter(HttpRequest), dict_tape(memory), github)
again = stacked(latest_releases(REPOS, "secret"), make_memo_rewriter(HttpRequest), dict_tape(memory), offline)
print("memory:", first == again, sent.total())

# With no storage handler, every request goes out (limit: "With no storage handler installed ...").
sent.clear()
for _ in range(2):
    stacked(latest_releases(REPOS, "secret"), make_memo_rewriter(HttpRequest), github)
print("no storage:", sent.total())

# A POST is recorded too, and not sent on replay (limit: "POST included").
@do
def publish():
    return (yield HttpRequest("POST", url))

sent.clear()
tape = directory / "post.json"
print("post:", recorded(publish(), tape, github) == recorded(publish(), tape, offline), sent.total())

# The token is not in the file (body: "With either key, the token is not in the file").
for name, key in (("default", None), ("method_and_url", method_and_url)):
    tape = directory / f"token-{name}.json"
    recorded(latest_releases(REPOS, "s3cr3t-token"), tape, github, key)
    stored = tape.read_bytes()
    print(f"token in {name}:", b"s3cr3t-token" in stored, b"v2.4.1" in stored)

# The default key is a hash of the pickled request, and it changes with the pickle protocol (limit: "a key made on
# Python 3.13 is not found on 3.14"; pickle's default protocol went from 4 to 5 in Python 3.14). The article does not
# name the default key's function, so it is read from the module that defines it.
import hashlib, pickle
from doeff_core_effects._memo_handlers import content_address
request = HttpRequest("GET", url, headers={"Authorization": "Bearer secret"})
by_protocol = {protocol: hashlib.sha256(pickle.dumps(request, protocol=protocol)).hexdigest() for protocol in (4, 5)}
print("key:", content_address(request) in by_protocol.values(), by_protocol[4] != by_protocol[5])
"""

# Another process replays from the same file (body: "A test in another process, or in CI, replays from that file the
# same way").
_RECORD = "import sys; from on_disk import *; print(recorded(latest_releases(REPOS, 'secret'), Path(sys.argv[1]), github))"
_REPLAY = "import sys; from on_disk import *; print(recorded(latest_releases(REPOS, 'secret'), Path(sys.argv[1]), offline))"


def test_the_claims_the_article_makes(deadline_scale_factor: float) -> None:
    claims = [
        tuple(line.split(": ", 1))
        for line in _child(["-c", _CLAIMS], deadline_scale_factor).splitlines()
    ]
    assert claims == [
        ("bare", "UnhandledEffect True"),
        ("hash", "TypeError"),
        ("equal", "False"),
        # Not recorded: ask whether it is there, perform outward, put the answer. Recorded: ask, then read (the
        # HttpRequest does not reach the storage handler).
        ("miss", "MemoExistsEffect"),
        ("miss", "HttpRequest"),
        ("miss", "MemoPutEffect"),
        ("hit", "MemoExistsEffect"),
        ("hit", "MemoGetEffect"),
        ("memory", "True 2"),
        ("no storage", "4"),
        ("post", "True 1"),
        ("token in default", "False True"),
        ("token in method_and_url", "False True"),
        ("key", "True True"),
    ]


def test_another_process_replays_from_the_same_file(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    tape = str(tmp_path / "releases.json")
    recorded = _child(["-c", _RECORD, tape], deadline_scale_factor)
    replayed = _child(["-c", _REPLAY, tape], deadline_scale_factor)
    assert recorded == replayed == "{'acme/server': 'v2.4.1', 'acme/client': 'v1.9.0'}"


def test_the_excerpt_check_finds_a_shortened_excerpt() -> None:
    source = "a = 1\n\n\nb = 2\nc = 3\n\n\nd = 4"
    assert excerpt_problem("a = 1\n\nd = 4", source) is None
    assert excerpt_problem("b = 2", source) is None
    assert excerpt_problem("d = 4\n\na = 1", source) == "chunk missing from file or out of order: 'a = 1'"
    assert excerpt_problem("b = 2\nd = 4", source) == "chunk missing from file or out of order: 'b = 2'"


def test_the_article_does_not_say_an_ai_wrote_it() -> None:
    lines = ARTICLE.read_text(encoding="utf-8").splitlines()
    found = [f"{n}: {line}" for n, line in enumerate(lines, 1) if _AI_AUTHORSHIP.search(line)]
    assert not found, "the article claims an AI wrote it:\n" + "\n".join(found)


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
    assert not found, "words reading as replacing I/O only, outside the I/O section:\n" + "\n".join(found)


def test_the_io_word_check_reads_sections() -> None:
    text = (
        "# Title\nSwap the client.\n## A recorder\na fake clock\n"
        "## I/O: a live server\nno real I/O, no mocks\n## Limits\nmock.patch\n"
    )
    assert [entry.split(": ")[1] for entry in io_words_outside_the_io_section(text)] == [
        "Swap",
        "fake",
        "mock",
    ]


def test_the_post_links_to_the_earlier_posts() -> None:
    # The previous post (the third), the second that the I/O section names, and the head of the series (the first). The
    # third post's "next post" link to this one belongs on the third post's side, when this one goes out.
    text = ARTICLE.read_text(encoding="utf-8")
    for slug in ("time-and-clocks.md", "yield-and-async.md", "algebraic-effects-in-python.md"):
        assert f"]({slug})" in text, slug
        assert (ARTICLE.parent / slug).is_file(), slug
