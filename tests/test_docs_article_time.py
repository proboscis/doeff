"""英語の記事 3 本目「Time as an effect」(docs/articles/time-as-an-effect.md)の例が、doeff 本体だけで(subpackage を
入れずに)そのまま走り、記事が見せる通りに印字する事の検(取り組み doeff-publish・card ki-440ef7b9f600・本体だけで出す形は
card ki-c34897b9eede)。時刻の effect(Delay・WaitUntil・GetTime)と時計の handler は例の clock.py が自分で書く。

囲みは 1 本目の記事(tests/test_docs_article_algebraic_effects.py)と同じ書き方のまま読む:

- 囲みは `~~~<言葉>` で始まる(``` の囲みは mermaid の図)。
- python の囲みの頭の行が `# <名>.py` なら、examples/time_as_an_effect のその file の全部。`# <名>.py (excerpt)` なら、空行で
  区切った塊がその file の中にそのままその順に在る。
- 頭の行の無い python の囲みは実行の行: clock.py・report.py の `__main__` の行か、実時間の行(20 秒かかるので走らせない)。
- text の囲みは、clock.py・report.py の印字の 1 区切り(見出し「-- <鍵>:」の下)のどれかと同じで、区切りは全部どこかの囲みに出る。

本文の主張のうち、走らせて確かめられる物も見る: handler 無しの nap は Delay で UnhandledEffect になる・仮想の時刻の印字は
毎回同じで 1 秒かからない・実時間の組でも同じ program が待つ順のまま起き、期限で build を打ち切る。記事の test_report.py は、
例の file と並べて pytest だけで通る事を見る。

例は既定で repo の環境の Python で走らせる。例は await_handler を値として使う(doeff main b8d242689 からの形・0.8.0 の wheel には
無い)。組んだ wheel で確かめる時は、子の Python の命令を DOEFF_ARTICLE_PYTHON に置く(例: `uv run --no-project --with
<doeff の wheel> --with <doeff-core-effects の wheel> --with <doeff-vm の wheel> --with pytest python` — 空白で区切る)。
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.cli.cli_child import with_settings

ROOT = Path(__file__).resolve().parents[1]
ARTICLE = ROOT / "docs" / "articles" / "time-as-an-effect.md"
EXAMPLES = ROOT / "examples" / "time_as_an_effect"
# 子 1 つの制限時間の、負荷で伸ばす前の値(秒)。1 本は 約 1 秒(多くは doeff の import)。PyPI の版を uv で入れる時は、
# 初めの 1 本が入れる時間を含む。
CHILD_DEADLINE_SECONDS = 120
# 記事の実時間の行(20 秒かかる — 記事に在る事だけを見て、走らせない。同じ組を短い待ちで走らせる検が下に在る)。
WALL_LINE = "print(run(real(two_naps())))"
# 記事の test_report.py を例の file と並べて走らせる時に写す、例の dir の file。
PYTEST_FILES = ("clock.py", "report.py", "test_report.py", "pytest.ini")

_FENCE = re.compile(r"^~~~(?P<language>\w*)\n(?P<body>.*?)^~~~$", re.MULTILINE | re.DOTALL)
_HEADER = re.compile(r"# (?P<name>\w+\.py)(?P<excerpt> \(excerpt\))?")
_SEGMENT = re.compile(r"^-- (.+):$\n", re.MULTILINE)


def _python() -> list[str]:
    """子の Python の命令(DOEFF_ARTICLE_PYTHON が在ればそれ、無ければこの検の Python)。"""
    return shlex.split(os.environ.get("DOEFF_ARTICLE_PYTHON", "")) or [sys.executable]


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
    return (EXAMPLES / name).read_text(encoding="utf-8").rstrip("\n")


def _child(command: list[str], cwd: Path, deadline_scale_factor: float, **settings: str | None):
    """読み手と同じく例の dir で子の interpreter を起こす(repo の conftest も pytest の plugin も読ませない)。"""
    return subprocess.run(
        with_settings(command, {"DOEFF_PROFILE": None, "PYTHONDONTWRITEBYTECODE": "1", **settings}),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
        check=False,
    )


def _printed(name: str, deadline_scale_factor: float) -> str:
    """例の file を `python <名>` で走らせた印字。"""
    done = _child([*_python(), "-W", "error", name], EXAMPLES, deadline_scale_factor)
    assert done.returncode == 0, done.stderr[-3000:]
    return done.stdout.rstrip("\n")


def _segments(name: str, stdout: str) -> dict[str, str]:
    """印字を、見出し「-- <鍵>:」ごとの行に分ける。"""
    parts = _SEGMENT.split(stdout)
    assert parts[0] == "", f"{name} の印字が見出しで始まらない: {parts[0]!r}"
    return {f"{name}:{parts[i]}": parts[i + 1].rstrip("\n") for i in range(1, len(parts), 2)}


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
    """頭の行の無い python の囲みに書ける行(clock.py・report.py の `__main__` の行と、実時間の行)。"""
    lines = {WALL_LINE}
    for name in ("clock.py", "report.py"):
        main = _source(name).split('\nif __name__ == "__main__":')[1]
        lines |= {line.strip() for line in main.splitlines()}
    return lines


_NAMED = [
    (body, line)
    for language, body, line in _fences()
    if language == "python" and _header(body)
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
    assert WALL_LINE in ARTICLE.read_text(encoding="utf-8")


def test_text_fences_are_what_the_examples_print(deadline_scale_factor: float) -> None:
    printed = {
        **_segments("clock.py", _printed("clock.py", deadline_scale_factor)),
        **_segments("report.py", _printed("report.py", deadline_scale_factor)),
    }
    texts = [(line, body) for language, body, line in _fences() if language == "text"]
    unmatched = [(line, body) for line, body in texts if body not in printed.values()]
    assert unmatched == [], f"記事の出力の囲みが、どの例の印字とも違う: {unmatched}"
    # 印字の区切りは全部、記事のどこかの囲みに出る(囲みが消えると上の検は黙って減る)
    unshown = [key for key, body in printed.items() if body not in {body for _, body in texts}]
    assert unshown == [], f"記事に出ない印字の区切り: {unshown}"


def test_the_article_test_passes_beside_the_example_files(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    for name in PYTEST_FILES:
        shutil.copy(EXAMPLES / name, tmp_path / name)
    done = _child(
        [*_python(), "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_report.py"],
        tmp_path,
        deadline_scale_factor,
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTEST_ADDOPTS=None,
    )
    assert done.returncode == 0, f"{done.stdout[-3000:]}\n{done.stderr[-3000:]}"
    assert re.match(r"1 passed\b", done.stdout.splitlines()[-1]), done.stdout


_CLAIMS = """
import time
from clock import *
from report import *

try:
    run(nap(20))
    print("bare: ran")
except Exception as error:
    print("bare:", type(error).__name__, str(error).splitlines()[0])

started = time.perf_counter()
runs = {repr(run(simulated(daily_report([3, 12, 5])))) for _ in range(3)}
print("simulated:", len(runs), time.perf_counter() - started)

print("wall:", run(real(two_naps(0.2, 0.1))))
started = time.perf_counter()
print("timeout:", run(real(within(0.2, build(1)))), time.perf_counter() - started)
print("in time:", run(real(within(0.2, build(0)))))
"""


def test_the_claims_the_article_makes(deadline_scale_factor: float) -> None:
    done = _child([*_python(), "-W", "error", "-c", _CLAIMS], EXAMPLES, deadline_scale_factor)
    assert done.returncode == 0, done.stderr[-3000:]
    bare, simulated, wall, timeout, in_time = done.stdout.splitlines()
    # handler 無しの nap は、最初の Delay で止まる(本文「it stops at its Delay with UnhandledEffect」)
    assert bare.startswith("bare: UnhandledEffect"), bare
    assert "Delay(seconds=20)" in bare, bare
    # 仮想の時刻の組は毎回同じ行を印字し、Friday から Wednesday までを待たずに終える(本文「a few milliseconds」 —
    # 負荷の下でも落ちない枠として、3 回で 1 秒を上限に見る)
    count, seconds = simulated.removeprefix("simulated: ").split()
    assert count == "1", simulated
    assert float(seconds) < 1.0 * deadline_scale_factor, simulated
    # 実時間の組でも同じ two_naps が待ちの順に起きる(本文「a little above [20.0, 10.0]」を短い待ちで)
    slow, fast = (float(value) for value in wall.removeprefix("wall: ").strip("[]").split(", "))
    assert 0.2 <= slow < 0.2 + 1.0 * deadline_scale_factor, wall
    assert 0.1 <= fast < slow, wall
    # 実時間の組でも within の期限が build を打ち切り、build の 60 秒を待たない
    result, seconds = timeout.removeprefix("timeout: ").rsplit(" ", 1)
    assert result == "timed out", timeout
    assert float(seconds) < 0.2 + 5.0 * deadline_scale_factor, timeout
    assert in_time == "in time: built in 0 min", in_time


# 記事と例は doeff 本体だけで走る: import してよいのは標準の library と、`pip install doeff` が入れる doeff・doeff_core_effects と、
# 例の file どうしだけ。install の行は doeff と pytest だけを入れる。await_handler は値のまま付け、() で呼ばない(card ki-c34897b9eede)。
_CORE_MODULES = frozenset({"doeff", "doeff_core_effects"})
_EXAMPLE_MODULES = frozenset({"clock", "report"})
_IMPORT = re.compile(r"^\s*(?:from\s+(?P<source>[\w.]+)\s+import\b|import\s+(?P<module>[\w.]+))", re.MULTILINE)
_INSTALL = re.compile(r"^\s*pip install\b.*$", re.MULTILINE)
_README = EXAMPLES / "README.md"


def _python_texts() -> dict[str, str]:
    """例の .py の file と、記事の python の囲み(どこに在るかの名 → 中身)。"""
    texts = {name: _source(name) for name in PYTEST_FILES if name.endswith(".py")}
    texts.update(
        (f"記事の {line} 行目", body) for language, body, line in _fences() if language == "python"
    )
    return texts


def imported_outside_core(text: str) -> list[str]:
    """標準の library・doeff 本体・例の file のどれでもない、import された頭の module の名。"""
    found = {(match["source"] or match["module"]).split(".")[0] for match in _IMPORT.finditer(text)}
    allowed = set(sys.stdlib_module_names) | _CORE_MODULES | _EXAMPLE_MODULES
    return sorted(found - allowed)


def test_the_examples_import_nothing_but_doeff_itself() -> None:
    outside = {where: names for where, text in _python_texts().items() if (names := imported_outside_core(text))}
    assert outside == {}, f"doeff 本体の外の module を import している: {outside}"


def test_the_install_lines_install_doeff_alone() -> None:
    lines = [
        line.split("#")[0].split()
        for path in (ARTICLE, _README)
        for line in _INSTALL.findall(path.read_text(encoding="utf-8"))
    ]
    assert lines, "記事と例の README に install の行が無い"
    assert all(words == ["pip", "install", "doeff", "pytest"] for words in lines), lines


def test_await_handler_is_installed_without_a_call() -> None:
    texts = {**_python_texts(), "記事": ARTICLE.read_text(encoding="utf-8")}
    called = [where for where, text in texts.items() if "await_handler()" in text]
    assert called == [], f"await_handler() と呼んでいる: {called}"


def test_the_import_check_finds_a_subpackage() -> None:
    assert imported_outside_core("from doeff_time import Delay\nimport heapq\n") == ["doeff_time"]
    assert imported_outside_core("import doeff_records.store\n") == ["doeff_records"]
    assert imported_outside_core("from doeff_core_effects.scheduler import scheduled\nfrom clock import nap\n") == []


def test_the_excerpt_check_finds_a_shortened_excerpt() -> None:
    source = "a = 1\n\n\nb = 2\nc = 3\n\n\nd = 4"
    assert excerpt_problem("a = 1\n\nd = 4", source) is None
    assert excerpt_problem("b = 2", source) is None
    assert excerpt_problem("d = 4\n\na = 1", source) == "塊が file に無いか順が違う: 'a = 1'"
    assert excerpt_problem("b = 2\nd = 4", source) == "塊が file に無いか順が違う: 'b = 2'"


# 記事の中に、AI が書いた・AI の agent が書いたと名乗る行を置かない(1 本目の記事の検と同じ形)。書き手を名乗る文の形だけを探す。
_AI_AUTHORSHIP = re.compile(
    r"\b(written|drafted|generated|authored)\s+(by|with)\s+(an?\s+)?(AI|LLM|agent|Claude|ChatGPT)\b"
    r"|\b(an?\s+)?(AI|LLM)(\s+agent)?\s+(wrote|drafted|generated|authored)\b",
    re.IGNORECASE,
)
# 記事の軸は algebraic effects で、時刻は handler が決める物として書く。IO の差し替えの話に読める語を置かない(この記事には
# IO の例の節が無いので、本文の全部で見る — 取り組み doeff-publish の区切り 27 の合格の条件)。
_IO_REPLACEMENT = re.compile(r"\b(swap|fake|mock)\w*|\breal\s+I/O\b", re.IGNORECASE)


def test_the_article_does_not_say_an_ai_wrote_it() -> None:
    lines = ARTICLE.read_text(encoding="utf-8").splitlines()
    found = [f"{n}: {line}" for n, line in enumerate(lines, 1) if _AI_AUTHORSHIP.search(line)]
    assert not found, "記事が AI の書き手を名乗っている:\n" + "\n".join(found)


def test_the_article_is_not_told_as_replacing_io() -> None:
    lines = ARTICLE.read_text(encoding="utf-8").splitlines()
    found = [f"{n}: {line}" for n, line in enumerate(lines, 1) if _IO_REPLACEMENT.search(line)]
    assert not found, "記事に IO の差し替えの語が在る:\n" + "\n".join(found)


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


@pytest.mark.parametrize(
    "line",
    [
        "Swap the clock for a test.",
        "a fake clock",
        "trio's MockClock",
        "the mocked sleep",
        "tests without real I/O",
    ],
)
def test_the_io_replacement_check_finds_the_words_it_forbids(line: str) -> None:
    assert _IO_REPLACEMENT.search(line)
