"""英語の記事 1 本目「Algebraic effects for Python」(docs/articles/algebraic-effects-in-python.md)の例が、今の doeff と
doeff-time でそのまま走り、記事が見せる通りに印字する事の検(取り組み doeff-publish・card ki-6f2a58632d02)。

記事は審査で ok になった版のまま置く(本文を検の約束に合わせて書き換えない)。そこで囲みは README の約束
(tools/readme_examples.py)でなく、記事の書き方のまま読む:

- 囲みは `~~~<言葉>` で始まる(``` の囲みは mermaid の図)。
- python の囲みの頭の行が `# <名>.py` なら、その file の全部。`# <名>.py (excerpt)` なら、空行で区切った塊がその file の中に
  そのままその順に在る。file は examples/effects_tour を先に、無ければ examples/testing_without_mocks から探す。
  例外は test_welcome.py: 記事の版は handler を `@handler` だけで書く。例の dir の版は、日次で PyPI の 0.7.0 を入れて走らせる
  ので `@handler @do` のまま(次の版を PyPI に出す時に書き替える)。記事の版は、例の dir の welcome.py・infra.py と並べて
  pytest で通る事を見る。
- 頭の行の無い python の囲みは実行の行: 行は monitor.py の `__main__` の行か、実時間の行(Layer 5 — 30 分かかるので走らせない)
  か、偽の handler を 1 つ忘れた行。
- text の囲みは、spend.py の印字・monitor.py の印字の 1 区切り(見出し「-- <鍵>: ...」の下)・忘れた時の doeff の traceback の
  頭のどれかと同じ。

本文の主張のうち、走らせて確かめられる物も見る: scheduler だけでは最初の Spend で UnhandledEffect になる・仮想の時刻の
順が毎回同じ・実時間の組の 1 回が待ち無しで終わる。
"""

from __future__ import annotations

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

_FENCE = re.compile(r"^~~~(?P<language>\w*)\n(?P<body>.*?)^~~~$", re.MULTILINE | re.DOTALL)
_HEADER = re.compile(r"# (?P<name>\w+\.py)(?P<excerpt> \(excerpt\))?")
_SEGMENT = re.compile(r"^-- (\w+): .*$\n", re.MULTILINE)
# 記事の Layer 5 の行(実時間で 30 分かかる — 記事に在る事だけを見て、走らせない)。
REAL_LINE = "run(real(printed(free(http_web(retrying(3)(monitor(URLS, 2)))))))"
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


def _child(command: list[str], cwd: Path, deadline_scale_factor: float, **settings: str | None):
    """読み手と同じく例の dir で子の interpreter を起こす(repo の conftest も pytest の plugin も読ませない)。"""
    return subprocess.run(
        with_settings(command, {"DOEFF_PROFILE": None, **settings}),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
        check=False,
    )


def _printed(name: str, deadline_scale_factor: float) -> str:
    """effects_tour の file を `python <名>` で走らせた印字。"""
    done = _child([sys.executable, "-W", "error", name], TOUR, deadline_scale_factor)
    assert (done.returncode, done.stderr) == (0, ""), done.stderr[-3000:]
    return done.stdout.rstrip("\n")


def _segments(stdout: str) -> dict[str, str]:
    """monitor.py の印字を、見出し「-- <鍵>: ...」ごとの行に分ける。"""
    parts = _SEGMENT.split(stdout)
    return {f"monitor.py:{parts[i]}": parts[i + 1].rstrip("\n") for i in range(1, len(parts), 2)}


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
    """頭の行の無い python の囲みに書ける行(monitor.py の `__main__` の行・実時間の行・忘れた行)。"""
    main = _source("monitor.py").split('\nif __name__ == "__main__":')[1]
    return {line.strip() for line in main.splitlines()} | {REAL_LINE, FORGOT.splitlines()[-1]}


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
        **_segments(_printed("monitor.py", deadline_scale_factor)),
    }
    unmatched = [
        (line, body)
        for language, body, line in _fences()
        if language == "text" and body not in printed.values()
    ]
    assert unmatched == [], f"記事の出力の囲みが、どの例の印字とも違う: {unmatched}"


def test_every_output_the_article_quotes_is_shown() -> None:
    # monitor.py の印字の区切りのうち記事が見せる 4 つと、spend.py・忘れた時の traceback。囲みが消えると上の検は黙って減る。
    texts = [body for language, body, _ in _fences() if language == "text"]
    assert len(texts) == 6
    assert REAL_LINE in ARTICLE.read_text(encoding="utf-8")


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
import contextlib, io
from monitor import *

def captured(program):
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink):
        result = run(program)
    return sink.getvalue() + repr(result)

try:
    run(scheduled(monitor(URLS, 1)))
    print("bare: ran")
except Exception as error:
    print("bare:", type(error).__name__, str(error).splitlines()[0])
runs = {captured(simulated(printed(free(fake_web(monitor(URLS, 2)))))) for _ in range(3)}
print("simulated:", len(runs))
print("real:", repr(captured(real(printed(free(http_web(retrying(3)(monitor(URLS, 1)))))))))
"""


def test_the_claims_the_article_makes_about_monitor(deadline_scale_factor: float) -> None:
    done = _child([sys.executable, "-c", _CLAIMS], TOUR, deadline_scale_factor)
    assert done.returncode == 0, done.stderr[-3000:]
    bare, simulated, real = done.stdout.splitlines()
    # scheduled だけでは、最初の Spend で止まる(本文「none of these effects has a meaning until a handler gives it one」)
    assert bare.startswith("bare: UnhandledEffect"), bare
    assert "Spend" in bare, bare
    # 仮想の時刻では、2 つの task の行が毎回同じ順に出る(本文「the same order on every run」)
    assert simulated == "simulated: 1", simulated
    # 実時間の組も同じ monitor を走らせる(待ちの無い 1 回 — 2 回目は 30 分を待つので走らせない)
    assert real.endswith("[[9], [15]]'"), real


def test_the_excerpt_check_finds_a_shortened_excerpt() -> None:
    source = "a = 1\n\n\nb = 2\nc = 3\n\n\nd = 4"
    assert excerpt_problem("a = 1\n\nd = 4", source) is None
    assert excerpt_problem("b = 2", source) is None
    assert excerpt_problem("d = 4\n\na = 1", source) == "塊が file に無いか順が違う: 'a = 1'"
    assert excerpt_problem("b = 2\nd = 4", source) == "塊が file に無いか順が違う: 'b = 2'"
