"""英語の記事 4 本目「Record and replay」(docs/articles/record-and-replay.md)の例が doeff だけで(subpackage も extra も
入れずに)そのまま走り、記事が見せる通りに印字する事の検(取り組み doeff-publish・card ki-e86c66488895・本体だけで出す形は
card ki-d86f13e78e21)。例は HttpRequest を自分の effect として書き、保存は make_memo_rewriter の 3 つの effect に答える
JSON の handler、送り手は urllib を Await で待つ handler — doeff-core-effects の HttpRequest・http_production_handler・
sqlite_memo_handler は Hy で書いてあり、extra の http・hy が要るので使わない。

囲みは 1 本目・2 本目の記事(tests/test_docs_article_algebraic_effects.py・tests/test_docs_article_yield_and_async.py)と同じ
書き方で読む:

- 囲みは `~~~<言葉>` で始まる(``` の囲みは mermaid の図)。
- python の囲みの頭の行が `# <名>.py` なら、examples/record_and_replay のその file の全部。`# <名>.py (excerpt)` なら、空行で
  区切った塊がその file の中にそのままその順に在る。
- 頭の行の無い python の囲みは実行の行: 行はどれかの例の file の `__main__` の行。
- text の囲みは、例の file の印字の 1 区切り(見出し「-- <鍵>: ...」の下)のどれかと同じ。逆に、どの区切りも記事のどこかの
  text の囲みが見せる(区切りを足して記事に書き忘れた時も赤にする)。

例を走らせる interpreter は、既定で検を走らせている物(repo の doeff)。環境変数 DOEFF_ARTICLE_PYTHON に、doeff だけを
入れた環境の python を渡すと、同じ検をその版で走らせる(記事は「pip install doeff だけでそのまま走る」と書く)。例は
`await_handler(program)` の形を使い、この形は 0.8.0 の wheel に無い(main b8d242689 から)ので、出るまでは main から組んだ
wheel を入れる。環境は残る dir に作る(`uv run --with` の環境は命令が終わると消えるので、その python の path は渡せない):

    uv build --package doeff --wheel --out-dir /tmp/main-wheels
    uv build --package doeff-core-effects --wheel --out-dir /tmp/main-wheels
    uv build --package doeff-vm --wheel --python 3.12 --out-dir /tmp/main-wheels
    uv venv --python 3.12 /tmp/main-wheels/py && uv pip install --python /tmp/main-wheels/py --find-links /tmp/main-wheels doeff
    DOEFF_ARTICLE_PYTHON=/tmp/main-wheels/py/bin/python uv run pytest tests/test_docs_article_record_and_replay.py

本文の主張のうち、走らせて確かめられる物も見る: 再生の組の外に HttpRequest の handler が無い・HttpRequest は hash を持たず、
token だけが違う 2 つは等しくない・memo の層が出す effect の順・同じ 3 つに辞書で答えれば記憶に置いても同じ・保存の handler が
無いと毎回外へ出る・POST も記録して再生で送らない・file に token が残らない・既定の鍵が pickle の protocol で変わる・
別の process が同じ file から再生する。

本文の決まりも見る: AI が書いたと名乗る行が無い・IO の差し替えだけに読める語(swap・fake・mock・real I/O)が見出し「## I/O」の
節の外に無い(軸は algebraic effects — 記事を IO の差し替えの話に縮めない)・前の回の記事と 1 本目の記事へ link する。
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
# 子 1 つの制限時間の、負荷で伸ばす前の値(秒)。1 本は約 1 秒(多くは doeff の import)。
CHILD_DEADLINE_SECONDS = 50
# 子の警告は誤りにする(await_handler を引数の無い factory として呼ぶ古い形の DeprecationWarning も、閉じ忘れの ResourceWarning も赤にする)。
WARNINGS = ["-W", "error"]

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


def _child(arguments: list[str], deadline_scale_factor: float) -> str:
    """読み手と同じく例の dir で子の interpreter を起こした印字(repo の conftest も pytest の plugin も読ませない)。"""
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
        for index, segment in enumerate(_segments(_child([name], deadline_scale_factor)))
    }
    texts = [(line, body) for language, body, line in _fences() if language == "text"]
    unmatched = [(line, body) for line, body in texts if body not in segments.values()]
    assert unmatched == [], f"記事の出力の囲みが、どの例の印字とも違う: {unmatched}"
    unshown = [key for key, segment in segments.items() if segment not in {b for _, b in texts}]
    assert unshown == [], f"例の印字の区切りのうち、記事が見せない物: {unshown}"


# 本文の主張を、例の file の部品で 1 つずつ印字する(1 行 1 主張・「<名>: <値>」— 名が重なる行は、出た順に並べて比べる)。
_CLAIMS = """
import contextlib, io, tempfile
from pathlib import Path

from doeff import Pass, Resume, do, handler, run
from doeff_core_effects.handlers import await_handler, slog_discard_handler
from doeff_core_effects.memo_effects import MemoExistsEffect, MemoGetEffect, MemoPutEffect
from doeff_core_effects.memo_handlers import make_memo_rewriter
from doeff_core_effects.scheduler import scheduled

from on_disk import json_tape, method_and_url, offline, recorded
from releases import REPOS, HttpRequest, github, latest_releases, sent

def stacked(program, *handlers):
    for install in handlers:
        program = install(program)
    return run(scheduled(await_handler(slog_discard_handler(program))))

# 再生の組の外には HttpRequest の handler が無い(本文「had replay passed a request on, ... UnhandledEffect」)。
# run が stderr へ書く doeff の traceback は、ここでは読まない
try:
    with contextlib.redirect_stderr(io.StringIO()):
        run(latest_releases(REPOS, "secret"))
    print("bare: ran")
except Exception as error:
    print("bare:", type(error).__name__, "HttpRequest" in str(error))

# 依頼そのものは tape の鍵にならない: headers が dict なので hash を持たず、token だけが違う 2 つは別の依頼
# (本文「its headers are a dict, so it has no hash, and two requests that differ only in the token are not equal」)
url = "https://api.github.com/repos/acme/server/releases/latest"
try:
    hash(HttpRequest("GET", url))
    print("hash: ok")
except TypeError:
    print("hash: TypeError")
print("equal:", HttpRequest("GET", url, {"Authorization": "Bearer a"}) == HttpRequest("GET", url, {"Authorization": "Bearer b"}))

# memo の層が保存の handler へ出す effect の順(本文の表の 1 行目 — log の Slog は除く)。通る effect を 1 つずつ印字する
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

# 同じ 3 つに辞書で答えれば、同じ層が記憶から再生する(本文「Answer the same three from a dict ...」)
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

# 保存の handler が無いと、毎回外へ出る(本文の限り「With no storage handler installed ...」)
sent.clear()
for _ in range(2):
    stacked(latest_releases(REPOS, "secret"), make_memo_rewriter(HttpRequest), github)
print("no storage:", sent.total())

# POST も記録し、再生では送らない(本文の限り「POST included」)
@do
def publish():
    return (yield HttpRequest("POST", url))

sent.clear()
tape = directory / "post.json"
print("post:", recorded(publish(), tape, github) == recorded(publish(), tape, offline), sent.total())

# file に token が残らない(本文「With either key, the token is not in the file」)
for name, key in (("default", None), ("method_and_url", method_and_url)):
    tape = directory / f"token-{name}.json"
    recorded(latest_releases(REPOS, "s3cr3t-token"), tape, github, *([key] if key else []))
    stored = tape.read_bytes()
    print(f"token in {name}:", b"s3cr3t-token" in stored, b"v2.4.1" in stored)

# 既定の鍵は pickle した依頼の hash で、pickle の protocol が変われば鍵も変わる(本文の限り「a key made on Python 3.13 is not
# found on 3.14」— pickle の既定の protocol は Python 3.14 で 4 から 5 になった)
import hashlib, pickle
from doeff_core_effects.memo_handlers import content_address
request = HttpRequest("GET", url, headers={"Authorization": "Bearer secret"})
by_protocol = {protocol: hashlib.sha256(pickle.dumps(request, protocol=protocol)).hexdigest() for protocol in (4, 5)}
print("key:", content_address(request) in by_protocol.values(), by_protocol[4] != by_protocol[5])
"""

# 別の process が同じ file から再生する(本文「A test in another process, or in CI, replays from that file the same way」)
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
        # 記録に無い時: 在るかを問い、外へ出して、答えを置く。在る時: 在るかを問い、読む(HttpRequest は保存の handler へ届かない)
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
        "# Title\nSwap the client.\n## A recorder\na fake clock\n"
        "## I/O: a live server\nno real I/O, no mocks\n## Limits\nmock.patch\n"
    )
    assert [entry.split(": ")[1] for entry in io_words_outside_the_io_section(text)] == [
        "Swap",
        "fake",
        "mock",
    ]


def test_the_post_links_to_the_earlier_posts() -> None:
    # 前の回(3 本目)・I/O の節が挙げる 2 本目・連なりの頭(1 本目)。3 本目から 4 本目への「次の回」の link は 3 本目の側に置く。
    text = ARTICLE.read_text(encoding="utf-8")
    for slug in ("time-as-an-effect.md", "yield-and-async.md", "algebraic-effects-in-python.md"):
        assert f"]({slug})" in text, slug
