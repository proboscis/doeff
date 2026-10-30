# PyPIで入れる物(../verify_pypi.pyが、PyPIのdoeffの版を決めて毎日走らせる)。
# /// script
# dependencies = [
#     "doeff",
# ]
# ///
"""replay記事(英語の4本目「Record and replay」の版)の例が、英語の版と同じコードで、載せたとおりに走る事を確かめる。

- 例は英語の版と同じ: 記事のPython・text・bashの枠は、コメントを除くと英語の記事(docs/articles/record-and-replay.md)の
  枠と1つずつ同じ(英語の版のmermaidの図は、記事では画像にした)。
- 例のファイルと同じ: 頭の行が`# <名>.py(抜粋)`の枠は、コメントを除くと、空行で区切った塊ごとに
  examples/record_and_replay/<名>.pyの中にその順で在る。頭の行の無い枠(実行の行)は、ちょうど1つの例のファイルの
  `__main__`の行。
- 載せたとおりに走る: 試し方の3つのファイルをこのPythonで走らせ、印字を見出し「-- <鍵>: ...」ごとの区切りに分ける。
  本文のtextの枠はどれも、どれかの区切りと同じ。区切りはどれも、本文のどれかのtextの枠に在る。
- 本文の主張: 走らせて確かめられる物を、例のファイルの部品で1つずつ印字して照らす(再生の外にHttpRequestのハンドラが
  無い・HttpRequestはハッシュを持たず、トークンだけが違う2つは等しくない・make_memo_rewriterが保存へ出すエフェクトの順・
  同じ3つに辞書で答えればメモリから再生する・
  保存のハンドラが無いと毎回外へ出る・POSTも記録して再生で送らない・ファイルにトークンが残らない・別のプロセスが
  同じファイルから再生する)。
- 本文の決まり: published: false、AIが書いたと名乗る文が無い、IOの差し替えだけに読める語がI/Oの節の外に無い、
  60字を越える文とです・ます以外で終わる文が無い、図が2〜4枚、前の回と1本目へのリンク、社外に出せない名前や鍵の形が無い。
"""

import importlib
import io
import itertools
import re
import subprocess
import sys
import tempfile
import tokenize
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
ROOT = SOURCE.parents[1]
EXAMPLES = ROOT / "examples/record_and_replay"
EXAMPLE_FILES = ("releases.py", "on_disk.py", "live.py")
sys.path.insert(0, str(SOURCE))  # 公開する本文を作る道具を、同じ数え方で使うため。
release = importlib.import_module("render_release")  # 同じdirのrender_release.py。

article = (SOURCE / "doeff-replay.md").read_text()
english = (ROOT / "docs/articles/record-and-replay.md").read_text()

FENCE = re.compile(r"^```(\w*)\n(.*?)^```$", re.S | re.M)
ENGLISH_FENCE = re.compile(r"^~~~(\w*)\n(.*?)^~~~$", re.S | re.M)
HEADER = re.compile(r"# (\w+\.py)\(抜粋\)")
SEGMENT = re.compile(r"^-- ([\w ]+): .*$\n", re.M)
# 子の警告は誤りにする(古い形の`await_handler()`のDeprecationWarningも、閉じ忘れのResourceWarningも止める)。
WARNINGS = ("-W", "error")
# IOの差し替えだけに読める語。I/Oの例の節の外で使えば、記事が代数的エフェクトの話からIOの差し替えの話に縮んで読める。
IO_ONLY_WORDS = re.compile(r"swap\w*|fake\w*|mock\w*|real I/O|差し替|モック|偽|本物のI/O", re.I)
# 記事の中に、AIが書いた・AIのエージェントが書いたと名乗る文を置かない。
AI_AUTHORSHIP = re.compile(
    r"\b(written|drafted|generated|authored)\s+(by|with)\s+(an?\s+)?(AI|LLM|agent|Claude|ChatGPT)\b"
    r"|\b(an?\s+)?(AI|LLM)(\s+agent)?\s+(wrote|drafted|generated|authored)\b"
    r"|(AI|LLM|エージェント|Claude|ChatGPT)[^。\n]{0,20}(書きました|書いた|生成し|下書きし)",
    re.I,
)


def without_comments(code: str, language: str) -> str:
    """コメントを除き、行末の空白を落とす(行の数と並びはそのまま)。"""
    lines = code.splitlines()
    if language == "bash":
        return "\n".join(line.split("#", 1)[0].rstrip() for line in lines)
    if language != "python":
        return "\n".join(line.rstrip() for line in lines)
    for token in tokenize.generate_tokens(io.StringIO(code).readline):
        if token.type == tokenize.COMMENT:
            row, column = token.start
            lines[row - 1] = lines[row - 1][:column]
    return "\n".join(line.rstrip() for line in lines)


def chunks(code: str) -> list[list[str]]:
    """空行で区切った塊の並び(塊の中の空行は除く)。抜粋は、例のファイルの塊を飛ばしてよいため。"""
    return [
        [line for line in block.splitlines() if line.strip()]
        for block in re.split(r"\n\s*\n", code.strip("\n"))
        if block.strip()
    ]


def excerpt_problem(excerpt: str, source: str) -> str | None:
    """抜粋の塊が、例のファイルの中にそのままその順で無ければ、その塊を言う文(在ればNone)。"""
    lines = source.splitlines()
    start = 0
    for chunk in chunks(excerpt):
        at = next(
            (
                i
                for i in range(start, len(lines) - len(chunk) + 1)
                if lines[i : i + len(chunk)] == chunk
            ),
            None,
        )
        if at is None:
            return f"塊が例のファイルに無いか、順が違う: {chunk[0]!r}"
        start = at + len(chunk)
    return None


def child(arguments: list[str]) -> str:
    """読み手と同じく例のdirで、このPythonの子を起こした印字。stderrに何か出れば止める。"""
    done = subprocess.run(
        [sys.executable, *WARNINGS, *arguments],
        cwd=EXAMPLES,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert (done.returncode, done.stderr) == (0, ""), (arguments[:1], done.stderr[-3000:])
    return done.stdout.rstrip("\n")


# 枠は英語の版と同じ: 数と言葉が同じで、コメントを除くと中身も同じ。
fences = FENCE.findall(re.sub(r"\A---\n.*?\n---\n", "", article, flags=re.S))
english_fences = ENGLISH_FENCE.findall(english)
assert [language for language, _ in fences] == [language for language, _ in english_fences], (
    "枠の数と種類が英語の版と違う",
    [language for language, _ in fences],
    [language for language, _ in english_fences],
)
for (language, body), (_, original) in zip(fences, english_fences, strict=True):
    assert without_comments(body, language) == without_comments(original, language), (
        "枠が英語の版と違う",
        body.splitlines()[0],
    )

# 抜粋は例のファイルの中に在り、実行の行はちょうど1つの例のファイルの__main__の行。
sources = {name: (EXAMPLES / name).read_text() for name in EXAMPLE_FILES}
main_lines = {
    name: {
        line.strip()
        for line in without_comments(
            source.split('\nif __name__ == "__main__":')[1], "python"
        ).splitlines()
        if line.strip()
    }
    for name, source in sources.items()
}
excerpts = 0
runs = 0
for index, (language, body) in enumerate(fences, 1):
    if language != "python":
        continue
    label = f"doeff-replay.md:block-{index}"
    header = HEADER.fullmatch(body.splitlines()[0])
    if header:
        name = header[1]
        assert name in sources, (label, f"例のファイル{name}が無い")
        problem = excerpt_problem(
            without_comments("\n".join(body.splitlines()[1:]), "python"),
            without_comments(sources[name], "python"),
        )
        assert problem is None, (label, problem)
        excerpts += 1
        continue
    lines = {line.strip() for line in without_comments(body, "python").splitlines() if line.strip()}
    owners = [name for name, allowed in main_lines.items() if lines <= allowed]
    assert len(owners) == 1, (
        label,
        "実行の行が、ちょうど1つの例のファイルの__main__の行ではない",
        owners,
    )
    runs += 1

# 試し方のとおりに例のファイルを走らせると、本文のtextの枠と印字の区切りが1つずつ対応する。
segments = {}
for name in EXAMPLE_FILES:
    parts = SEGMENT.split(child([name]) + "\n")
    assert parts[0] == "", (name, "区切りの見出しの前に印字が在る", parts[0])
    for number, segment in enumerate(parts[2::2]):
        segments[f"{name}#{number}"] = segment.rstrip("\n")
texts = [body.rstrip("\n") for language, body in fences if language == "text"]
unmatched = [text for text in texts if text not in segments.values()]
assert not unmatched, ("本文のtextの枠が、どの例の印字とも違う", unmatched)
unshown = [key for key, segment in segments.items() if segment not in texts]
assert not unshown, ("例の印字の区切りのうち、本文が見せない物", unshown)

# 本文の主張を、例のファイルの部品で1つずつ印字する(1行1主張・「<名>: <値>」)。
CLAIMS = """
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

# 再生の係の外にHttpRequestのハンドラが無ければ、UnhandledEffectで止まる(本文「doeffはUnhandledEffectで走りを止めます」)。
try:
    with contextlib.redirect_stderr(io.StringIO()):
        run(latest_releases(REPOS, "secret"))
    print("bare: ran")
except Exception as error:
    print("bare:", type(error).__name__, "HttpRequest" in str(error))

# 依頼そのものはtapeの鍵にならない: ヘッダが辞書なのでハッシュが無く、トークンだけが違う2つは等しくない
# (本文「ハッシュが無く、辞書の鍵になれません」「トークンだけが違う2つの依頼も、等しくなりません」)。
url = "https://api.github.com/repos/acme/server/releases/latest"
try:
    hash(HttpRequest("GET", url))
    print("hash: ok")
except TypeError:
    print("hash: TypeError")
print("equal:", HttpRequest("GET", url, {"Authorization": "Bearer a"}) == HttpRequest("GET", url, {"Authorization": "Bearer b"}))

# make_memo_rewriterが保存のハンドラへ出すエフェクトの順(本文の表の1行目と図の2枚目)。ログのSlogは除く。
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

# 同じ3つに辞書で答えれば、同じmake_memo_rewriterがメモリから再生する(本文「同じ3つに辞書で答えれば」)。
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

# 保存のハンドラが無いと、毎回外へ出る(本文の限界「保存のハンドラを付けないと」)。
sent.clear()
for _ in range(2):
    stacked(latest_releases(REPOS, "secret"), make_memo_rewriter(HttpRequest), github)
print("no storage:", sent.total())

# POSTも記録し、再生では送り直さない(本文の限界「POSTも含めて」)。
@do
def publish():
    return (yield HttpRequest("POST", url))

sent.clear()
tape = directory / "post.json"
print("post:", recorded(publish(), tape, github) == recorded(publish(), tape, offline), sent.total())

# ファイルにトークンが残らない(本文「どちらの鍵でも、ファイルにトークンは残りません」)。
for name, key in (("default", None), ("method_and_url", method_and_url)):
    tape = directory / f"token-{name}.json"
    recorded(latest_releases(REPOS, "s3cr3t-token"), tape, github, *([key] if key else []))
    stored = tape.read_bytes()
    print(f"token in {name}:", b"s3cr3t-token" in stored, b"v2.4.1" in stored)

# 既定の鍵はpickleした依頼のハッシュで、pickleのprotocolが変われば鍵も変わる(本文の限界「Python 3.13で作った鍵が3.14では当たりません」)。
# pickleの既定のprotocolは、Python 3.14で4から5になった。
import hashlib, pickle
from doeff_core_effects.memo_handlers import content_address
request = HttpRequest("GET", url, headers={"Authorization": "Bearer secret"})
by_protocol = {protocol: hashlib.sha256(pickle.dumps(request, protocol=protocol)).hexdigest() for protocol in (4, 5)}
print("key:", content_address(request) in by_protocol.values(), by_protocol[4] != by_protocol[5])
"""
claims = [tuple(line.split(": ", 1)) for line in child(["-c", CLAIMS]).splitlines()]
assert claims == [
    ("bare", "UnhandledEffect True"),
    ("hash", "TypeError"),
    ("equal", "False"),
    # 保存に無い時: 在るかを尋ね、外へ出し、答えを置く。在る時: 在るかを尋ね、読む(HttpRequestは保存のハンドラへ届かない)。
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
], claims

# 別のプロセスが、同じファイルから再生する(本文「別のプロセスのテストやCIも、同じようにそのファイルから再生します」)。
RECORD = "import sys; from on_disk import *; print(recorded(latest_releases(REPOS, 'secret'), Path(sys.argv[1]), github))"
REPLAY = "import sys; from on_disk import *; print(recorded(latest_releases(REPOS, 'secret'), Path(sys.argv[1]), offline))"
with tempfile.TemporaryDirectory(prefix="replay-check-") as directory:
    tape = str(Path(directory) / "releases.json")
    recorded_once = child(["-c", RECORD, tape])
    replayed = child(["-c", REPLAY, tape])
assert recorded_once == replayed == "{'acme/server': 'v2.4.1', 'acme/client': 'v1.9.0'}", (
    recorded_once,
    replayed,
)


# 本文の決まり。
def io_words_outside_the_io_section(text: str) -> list[str]:
    """見出しにI/Oを含む節の外に在る、IOの差し替えだけに読める語の行。"""
    lines = text.splitlines()
    headings = list(
        itertools.accumulate(
            lines, lambda heading, line: line if line.startswith("## ") else heading, initial=""
        )
    )[1:]
    return [
        f"{number}: {line}"
        for number, (line, heading) in enumerate(zip(lines, headings, strict=True), 1)
        if "I/O" not in heading and IO_ONLY_WORDS.search(line)
    ]


assert "published: false" in article
# ほかの回へのリンクの文字は、その回の題で、この記事の書き手を言う文ではない。前の回の題
# 「ライブラリのcommitの68%を、Claude Codeと書いた1年」は、ライブラリのcommitの話なので、照らす前に外す。
own_words = re.sub(r"\[[^\]]*\]\(doeff-[a-z]+\.md\)", "", article)
assert not AI_AUTHORSHIP.search(own_words), AI_AUTHORSHIP.findall(own_words)
outside = io_words_outside_the_io_section(article)
assert not outside, outside
# 英語の版と同じく、時計の回(3本目)・yieldとasyncの回(9本目)・1本目へリンクする。
for linked in ("doeff-time.md", "doeff-coroutines.md", "doeff-main.md"):
    assert f"]({linked})" in article, linked
released = release.release_text(
    article, release.read_rows((SOURCE / "publication-plan.md").read_text())
)
measured = release.measure(released)
assert 2 <= measured.images <= 4, measured.images
assert not measured.long_sentences, measured.long_sentences
assert not measured.plain_endings, measured.plain_endings
# 社外に出せない名前と、鍵やトークンの形。
forbidden = re.compile(
    r"agora|zeus|atlas|cisco|/home/|/Users/|sk-[A-Za-z0-9]|ghp_|ANTHROPIC_API_KEY|機体|答え手|名札",
    re.I,
)
assert not forbidden.search(article), forbidden.findall(article)

print(
    f"OK: replay記事 枠{len(fences)}個が英語の版と同じ・抜粋{excerpts}枠が例のファイルに在る・"
    f"実行の行{runs}枠が例の__main__の行・textの枠{len(texts)}個と例の印字の区切り{len(segments)}個が対応・"
    f"主張{len(claims)}行と別のプロセスでの再生が本文のとおり / "
    f"本文{measured.body_chars}字・図{measured.images}・60字を越える文{len(measured.long_sentences)}・"
    f"です・ます以外{len(measured.plain_endings)}"
)
