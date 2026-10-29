# PyPIで入れる物(../verify_pypi.pyが、PyPIのdoeffの版を決めて毎日走らせる)。
# /// script
# dependencies = [
#     "doeff",
# ]
# ///
"""coroutines記事(英語の2本目「Yield and async」の版)の例が、英語の版と同じコードで、載せたとおりに走る事を確かめる。

- 例は英語の版と同じ: 記事のPython・text・bashの枠は、コメントを除くと英語の記事(docs/articles/yield-and-async.md)の
  枠と1つずつ同じ(英語の版のmermaidの図は、記事では画像にした)。
- 例のファイルと同じ: 頭の行が`# <名>.py(抜粋)`の枠は、コメントを除くと、空行で区切った塊ごとに
  examples/yield_and_async/<名>.pyの中にその順で在る。頭の行の無い枠(実行の行)は、どれかの例のファイルの`__main__`の行。
- 載せたとおりに走る: 枠を上から順に、例のファイルごとの名前空間で走らせ、実行の枠の印字を直後のtextの枠と照らす。
  例外で止まる行は、印字に続けて「型: 文」を出した物として照らす。試し方の3つのファイルもこのPythonで走らせ、印字の区切りが
  どれも本文のtextの枠に在る事を見る。
- 本文の決まり: published: false、AIが書いたと名乗る文が無い、IOの差し替えだけに読める語がI/Oの節の外に無い、
  60字を越える文とです・ます以外で終わる文が無い、図が2〜4枚、社外に出せない名前や鍵の形が無い。
"""

import contextlib
import importlib
import io
import itertools
import re
import subprocess
import sys
import tokenize
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
ROOT = SOURCE.parents[1]
EXAMPLES = ROOT / "examples/yield_and_async"
sys.path.insert(0, str(SOURCE))  # 公開する本文を作る道具を、同じ数え方で使うため。
release = importlib.import_module("render_release")  # 同じdirのrender_release.py。

article = (SOURCE / "doeff-coroutines.md").read_text()
english = (ROOT / "docs/articles/yield-and-async.md").read_text()

FENCE = re.compile(r"^```(\w*)\n(.*?)^```$", re.S | re.M)
ENGLISH_FENCE = re.compile(r"^~~~(\w*)\n(.*?)^~~~$", re.S | re.M)
HEADER = re.compile(r"# (\w+\.py)\(抜粋\)")
SEGMENT = re.compile(r"^-- ([\w ]+): .*$\n", re.M)
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

# 抜粋は例のファイルの中に在り、実行の行は例のファイルの__main__の行。
sources = {path.name: path.read_text() for path in sorted(EXAMPLES.glob("*.py"))}
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


def file_of_run(body: str) -> str:
    """実行の行の枠が、どの例のファイルの__main__の行かを返す(その名前空間で走らせるため)。"""
    lines = {line.strip() for line in without_comments(body, "python").splitlines() if line.strip()}
    owners = [name for name, allowed in main_lines.items() if lines <= allowed]
    assert len(owners) == 1, (
        "実行の行の枠が、ちょうど1つの例のファイルの__main__の行ではない",
        body,
        owners,
    )
    return owners[0]


def printed(code: str, namespace: dict, label: str) -> str:
    """コードを名前空間で走らせ、標準出力に出た物を返す。例外で止まれば、「型: 文」を続けて出した物にする。"""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            exec(compile(code, label, "exec"), namespace)
        except Exception as error:  # 本文は、例外の型と文をtextの枠で見せる。
            print(f"{type(error).__name__}: {error}")
    return out.getvalue().rstrip("\n")


namespaces: dict[str, dict] = {name: {"__name__": name.removesuffix(".py")} for name in sources}
ran = 0
excerpts = 0
for index, (language, body) in enumerate(fences):
    if language != "python":
        continue
    label = f"doeff-coroutines.md:block-{index + 1}"
    header = HEADER.fullmatch(body.splitlines()[0])
    if header:
        name = header[1]
        assert name in sources, (label, f"例のファイル{name}が無い")
        problem = excerpt_problem(
            without_comments("\n".join(body.splitlines()[1:]), "python"),
            without_comments(sources[name], "python"),
        )
        assert problem is None, (label, problem)
        output = printed(body, namespaces[name], label)
        assert output == "", (label, "定義の枠が印字した", output)
        excerpts += 1
        continue
    name = file_of_run(body)
    following = fences[index + 1] if index + 1 < len(fences) else ("", "")
    assert following[0] == "text", (label, "実行の行の枠の直後に、textの枠が無い")
    output = printed(body, namespaces[name], label)
    assert output == following[1].rstrip("\n"), (label, output, following[1])
    ran += 1

# 試し方のとおりに例のファイルを走らせると、印字の区切りがどれも本文のtextの枠に在る。
texts = {body.rstrip("\n") for language, body in fences if language == "text"}
segments = 0
for name in ("greet.py", "drive.py", "stream.py"):
    done = subprocess.run(
        [sys.executable, "-W", "error", name],
        cwd=EXAMPLES,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert (done.returncode, done.stderr) == (0, ""), (name, done.stderr[-3000:])
    parts = SEGMENT.split(done.stdout)
    assert parts[0] == "", (name, "区切りの見出しの前に印字が在る", parts[0])
    for segment in parts[2::2]:
        assert segment.rstrip("\n") in texts, (name, "本文に無い印字の区切り", segment)
        segments += 1


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
assert not AI_AUTHORSHIP.search(article), AI_AUTHORSHIP.findall(article)
outside = io_words_outside_the_io_section(article)
assert not outside, outside
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
    f"OK: coroutines記事 枠{len(fences)}個が英語の版と同じ・抜粋{excerpts}枠が例のファイルに在る・"
    f"実行の行{ran}枠の印字が本文のとおり・例のファイルの印字の区切り{segments}個が本文に在る / "
    f"本文{measured.body_chars}字・図{measured.images}・60字を越える文{len(measured.long_sentences)}・"
    f"です・ます以外{len(measured.plain_endings)}"
)
