"""英語の記事 1 本目「Algebraic effects for Python」(docs/articles/algebraic-effects-in-python.md)の例が、今の doeff
だけで(subpackage を入れずに)そのまま走り、記事が見せる通りに印字する事の検(取り組み doeff-publish。
例は doeff 本体だけで出す)。

記事は審査で ok になった版のまま置く(本文を検の約束に合わせて書き換えない)。そこで囲みは README の約束
(tools/readme_examples.py)でなく、記事の書き方のまま読む:

- 囲みは `~~~<言葉>` で始まる(``` の囲みは mermaid の図)。
- python の囲みの頭の行が `# <名>.py` なら、その file の全部。`# <名>.py (excerpt)` なら、空行で区切った塊がその file の中に
  そのままその順に在る。file は examples/effects_tour を先に、無ければ examples/testing_without_mocks から探す。
  例外は test_welcome.py: 記事の版は handler を `@handler` だけで書く。例の dir の版は、日次で PyPI の 0.7.0 を入れて走らせる
  ので `@handler @do` のまま(次の版を PyPI に出す時に書き替える)。記事の版は、例の dir の welcome.py・infra.py と並べて
  pytest で通る事を見る。
- 頭の行の無い python の囲みは実行の行: 行は spire.py の tour() か play() の文(印字の頭の `"  ->"` を外した形も可)か、
  偽の handler を 1 つ忘れた行。
- text の囲みは、spend.py の印字・spire.py の印字の 1 区切り(見出し「<数>. ...」の下。行の頭の `  -> ` を外す)・忘れた時の
  doeff の traceback の頭のどれかと同じ。

[removed]
監視の例 monitor.py は前半に残さない)。本文の主張のうち、走らせて確かめられる物も見る: handler 無しでは最初の Roll で
UnhandledEffect になる・同じ seed の戦いは毎回同じ・Monte Carlo の handler の中の run は外の dice を使わない。
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
MOCKS = ROOT / "examples" / "testing_without_mocks"
# 子 1 つの制限時間の、負荷で伸ばす前の値(秒)。1 本は 約 1 秒(多くは doeff の import)。
CHILD_DEADLINE_SECONDS = 50
# spire.py の tour は約 25 秒(Monte Carlo の 100 戦が大半)。spire.py の頭の約束は 60 秒以内。
SPIRE_DEADLINE_SECONDS = 120

_FENCE = re.compile(r"^~~~(?P<language>\w*)\n(?P<body>.*?)^~~~$", re.MULTILINE | re.DOTALL)
_HEADER = re.compile(r"# (?P<name>\w+\.py)(?P<excerpt> \(excerpt\))?")
_SEGMENT = re.compile(r"^(\d+)\. .*$\n", re.MULTILINE)
# tour() の印字の頭の印。記事は印を外した行と印字を見せる。
ARROW = "  -> "
# 記事の前半の範囲と、そこに残してはいけない監視の例の言葉。
FRONT = ("## The idea", "## One mechanism under all of it")
MONITOR_WORDS = re.compile(r"two pages|half(?:-| )an? hour|half hour|\bmonitor", re.IGNORECASE)
# 記事の「Tests are one of the stacks」の、偽の handler を 1 つ忘れた行。
FORGOT = (
    "from doeff import run\n"
    "from test_welcome import fake_load_user\n"
    "from welcome import welcome\n"
    "run(fake_load_user(welcome(1)))\n"
)
# 記事の test_welcome.py を例の dir の版と並べて走らせる時に写す、例の dir の file。
MOCKS_FILES = ("welcome.py", "infra.py", "pytest.ini")


def _fences() -> list[tuple[str, str, int]]:
    """記事の `~~~` の囲み(言葉・中身・始まりの行)。"""
    text = ARTICLE.read_text(encoding="utf-8")
    return [
        (found["language"], found["body"].rstrip("\n"), text.count("\n", 0, found.start()) + 1)
        for found in _FENCE.finditer(text)
    ]


def _header(body: str) -> re.Match[str] | None:
    """python の囲みの頭の行 `# <名>.py` か `# <名>.py (excerpt)`(無ければ None)。"""
    return _HEADER.fullmatch(body.splitlines()[0]) if body else None


def _source(name: str) -> str:
    """囲みの頭の行が名指す例の file の中身(effects_tour を先に探す)。"""
    path = TOUR / name if (TOUR / name).exists() else MOCKS / name
    return path.read_text(encoding="utf-8").rstrip("\n")


def _child(
    command: list[str],
    cwd: Path,
    deadline_scale_factor: float,
    deadline_seconds: float = CHILD_DEADLINE_SECONDS,
    **settings: str | None,
):
    """読み手と同じく例の dir で子の interpreter を起こす(repo の conftest も pytest の plugin も読ませない)。"""
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
    """effects_tour の file を `python <名>` で走らせた印字。"""
    done = _child(
        [sys.executable, "-W", "error", name], TOUR, deadline_scale_factor, deadline_seconds
    )
    assert (done.returncode, done.stderr) == (0, ""), done.stderr[-3000:]
    return done.stdout.rstrip("\n")


def _unarrowed(text: str) -> str:
    """tour() の印字の行の頭の `  -> ` を外す。"""
    return "\n".join(line.removeprefix(ARROW) for line in text.splitlines())


def _segments(stdout: str) -> dict[str, str]:
    """spire.py の印字を、見出し「<数>. ...」ごとの行(頭の `  -> ` を外した形)に分ける。"""
    parts = _SEGMENT.split(stdout)
    return {
        f"spire.py:{parts[i]}": _unarrowed(parts[i + 1].rstrip("\n"))
        for i in range(1, len(parts), 2)
    }


def _chunks(text: str) -> list[list[str]]:
    """抜き書きを、file の中で続いて探す単位(空行で区切った塊)に分ける。"""
    return [
        [line for line in block.splitlines() if line.strip()]
        for block in re.split(r"\n\s*\n", text.strip("\n"))
        if block.strip()
    ]


def excerpt_problem(excerpt: str, source: str) -> str | None:
    """抜き書きの塊が、file の中にそのままその順に無ければ、その塊を言う文(在れば None)。"""
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


def _main_lines() -> set[str]:
    """頭の行の無い python の囲みに書ける行(spire.py の tour() と play() の文と、その印字の頭の印を外した形・忘れた行)。"""
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


def _welcome_dir(directory: Path) -> Path:
    """記事の test_welcome.py を、例の dir の welcome.py・infra.py と並べた dir を directory に作る。"""
    for name in MOCKS_FILES:
        shutil.copy(MOCKS / name, directory / name)
    (body,) = [body for _, body, _ in _fences() if body.startswith("# test_welcome.py\n")]
    (directory / "test_welcome.py").write_text(body + "\n", encoding="utf-8")
    return directory


# 記事の test_welcome.py は handler を @handler だけで書き、例の dir の版と違う — 下の pytest の検が受け持つ。
_NAMED = [
    (body, line)
    for language, body, line in _fences()
    if language == "python" and (header := _header(body)) and header["name"] != "test_welcome.py"
]


@pytest.mark.parametrize(("body", "line"), _NAMED, ids=[f"line{line}" for _, line in _NAMED])
def test_named_fences_are_the_example_files(body: str, line: int) -> None:
    header = _header(body)
    assert header is not None
    name = header["name"]
    source = _source(name)
    if header["excerpt"]:
        problem = excerpt_problem("\n".join(body.splitlines()[1:]), source)
        assert problem is None, f"記事の {line} 行目: {problem}"
    else:
        assert body == source, f"記事の {line} 行目の {name} が、例の file の全部と違う"


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


def test_text_fences_are_what_the_examples_print(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    welcome = _welcome_dir(tmp_path)
    forgot = _child([sys.executable, "-c", FORGOT], welcome, deadline_scale_factor).stderr
    printed = {
        "spend.py": _printed("spend.py", deadline_scale_factor),
        # 忘れた時は、Python の traceback の前に doeff が印字する頭だけを記事が見せる
        "forgot": forgot.strip("\n").split("\nTraceback (most recent call last):")[0].strip("\n"),
        **_segments(_printed("spire.py", deadline_scale_factor, SPIRE_DEADLINE_SECONDS)),
    }
    unmatched = [
        (line, body)
        for language, body, line in _fences()
        if language == "text" and body not in printed.values()
    ]
    assert unmatched == [], f"記事の出力の囲みが、どの例の印字とも違う: {unmatched}"


def test_every_output_the_article_quotes_is_shown() -> None:
    # spire.py の印字の 6 つの区切り(Layer 1〜6)と、spend.py・忘れた時の traceback。囲みが消えると上の検は黙って減る。
    texts = [body for language, body, _ in _fences() if language == "text"]
    assert len(texts) == 8


def _front() -> str:
    """記事の前半(The idea〜The stacks side by side)。"""
    text = ARTICLE.read_text(encoding="utf-8")
    return text[text.index(FRONT[0]) : text.index(FRONT[1])]


def test_the_front_half_is_the_battle_and_not_the_monitor() -> None:
    front = _front()
    found = [line for line in front.splitlines() if MONITOR_WORDS.search(line)]
    assert found == [], f"記事の前半に監視の例の文が残っている: {found}"
    # Layer 1〜6 と本番の節が spire.py の stack の順に並ぶ
    headings = re.findall(r"^### (.+)$", front, re.MULTILINE)
    assert [h.split(":")[0] for h in headings] == [
        *(f"Layer {n}" for n in range(1, 7)),
        "Production",
        "The stacks side by side",
    ]
    # 例の file の抜き書きは spire.py から(monitor.py を前半で引かない)
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
    welcome_dir = _welcome_dir(tmp_path)
    done = _child(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_welcome.py"],
        welcome_dir,
        deadline_scale_factor,
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTEST_ADDOPTS=None,
    )
    assert done.returncode == 0, f"{done.stdout[-3000:]}\n{done.stderr[-3000:]}"
    assert re.match(r"1 passed\b", done.stdout.splitlines()[-1]), done.stdout


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
# Monte Carlo の handler の中の run は自分の dice で答える: 外の dice の数の列は random bot の時と同じ形で進む
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
    # handler 無しでは、最初の Roll で止まる(本文「none of these effects has a meaning until a handler gives it one」)
    assert bare.startswith("bare: UnhandledEffect"), bare
    assert "Roll" in bare, bare
    # 同じ seed と同じ script の戦いは毎回同じ(本文「the same fight on every run」)
    assert same == "same seed: 1", same
    # playout の run の Roll は外の dice に届かない(本文「they do not use up the dice of the paused fight」)
    assert outer == "outer rolls: True True", outer


def test_the_excerpt_check_finds_a_shortened_excerpt() -> None:
    source = "a = 1\n\n\nb = 2\nc = 3\n\n\nd = 4"
    assert excerpt_problem("a = 1\n\nd = 4", source) is None
    assert excerpt_problem("b = 2", source) is None
    assert excerpt_problem("d = 4\n\na = 1", source) == "塊が file に無いか順が違う: 'a = 1'"
    assert excerpt_problem("b = 2\nd = 4", source) == "塊が file に無いか順が違う: 'b = 2'"


# 記事の中に、AI が書いた・AI の agent が書いたと名乗る行を置かない(2026-10-22 の決定:
# 記事に、AI が書いたという断り書きを置かない)。書き手を名乗る文の形だけを探す。
_AI_AUTHORSHIP = re.compile(
    r"\b(written|drafted|generated|authored)\s+(by|with)\s+(an?\s+)?(AI|LLM|agent|Claude|ChatGPT)\b"
    r"|\b(an?\s+)?(AI|LLM)(\s+agent)?\s+(wrote|drafted|generated|authored)\b",
    re.IGNORECASE,
)


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
