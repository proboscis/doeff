# PyPIで入れる物(../verify_pypi.pyが、PyPIのdoeffの版を決めて毎日走らせる)。
# /// script
# dependencies = [
#     "doeff",
# ]
# ///
"""メイン記事(英語の1本目「Algebraic effects for Python」の訳)の例が、英語の版と同じコードで、載せたとおりに走る事を確かめる。

- 例は英語の版と同じ: 記事のPython・bashの枠は、コメントを除くと英語の記事(docs/articles/algebraic-effects-in-python.md)の
  枠と1つずつ同じ。textの枠(印字)も同じで、違ってよいのは、doeffのtracebackが写すソースの行の日本語コメントだけ。
- 載せたとおりに走る: spend.pyを走らせた印字、monitor.pyの抜粋を順に読んだ後の実行の行の印字、test_welcome.pyの
  テスト、welcome.pyでハンドラを1つ付け忘れた時のtracebackの頭を、直後のtextの枠と照らす。実時間の行(30分かかる)は、
  1回ずつの確かめに替えて待ち無しで走らせる。scheduledだけでは最初のSpendでUnhandledEffectになる事も見る。
- 目次: 困りごとの表の回と、doeffの連載の10本の一覧を、公開計画の表と照らす(1本目は目次を兼ねる)。
"""

import contextlib
import io
import re
import subprocess
import sys
import tempfile
import tokenize
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
ROOT = SOURCE.parents[1]
article = (SOURCE / "doeff-main.md").read_text()
english = (ROOT / "docs/articles/algebraic-effects-in-python.md").read_text()

FENCE = re.compile(r"^```(python|text|bash)\n(.*?)^```$", re.S | re.M)
ENGLISH_FENCE = re.compile(r"^~~~(python|text|bash)\n(.*?)^~~~$", re.S | re.M)
# 実時間の行。実際に30分かかるので、1回ずつの確かめ(待ちが無い)に替えて走らせる。
REAL = "run(real(printed(free(http_web(retrying(3)(monitor(URLS, 2)))))))"
# ハンドラを1つ付け忘れた行。welcome.pyで付け忘れた時の印字を照らす。
FORGOT = "run(fake_load_user(welcome(1)))"


def without_comments(code: str, language: str) -> str:
    """コメントを除き、行末の空白を落とす(行の数と並びはそのまま)。"""
    lines = code.splitlines()
    if language == "bash":
        return "\n".join(line.split("#", 1)[0].rstrip() for line in lines)
    for token in tokenize.generate_tokens(io.StringIO(code).readline):
        if token.type == tokenize.COMMENT:
            row, column = token.start
            lines[row - 1] = lines[row - 1][:column]
    return "\n".join(line.rstrip() for line in lines)


fences = [(language, body) for language, body in FENCE.findall(article)]
english_fences = [(language, body) for language, body in ENGLISH_FENCE.findall(english)]
assert [language for language, _ in fences] == [language for language, _ in english_fences], (
    "枠の数と種類が英語の版と違う"
)
for (language, body), (_, original) in zip(fences, english_fences, strict=True):
    if language == "text":
        for line, english_line in zip(body.splitlines(), original.splitlines(), strict=True):
            assert line == english_line or line.startswith(english_line + "  # "), (
                line,
                english_line,
            )
    else:
        assert without_comments(body, language) == without_comments(original, language), (
            body.splitlines()[0]
        )


def printed(code: str, namespace: dict) -> str:
    """コードを名前空間で走らせ、標準出力に出た物を返す。"""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(compile(code, "doeff-main.md", "exec"), namespace)
    return out.getvalue()


def header(body: str) -> str:
    """Pythonの枠の頭の行の名(# spend.pyならspend.py)。頭の行の無い実行の行は空。"""
    found = re.fullmatch(r"# (\w+\.py)(?:\(抜粋\))?", body.splitlines()[0])
    return found[1] if found else ""


ran = 0
monitor_namespace: dict = {"__name__": "monitor"}
welcome_dir = Path(tempfile.mkdtemp(prefix="doeff-main-check-"))
for index, (language, body) in enumerate(fences):
    following = (
        fences[index + 1][1] if index + 1 < len(fences) and fences[index + 1][0] == "text" else None
    )
    if language != "python":
        continue
    name = header(body)
    if name == "spend.py":
        assert printed(body, {"__name__": "__main__"}) == following, "spend.pyの印字が本文と違う"
        ran += 1
    elif name == "monitor.py":
        printed(body, monitor_namespace)
    elif name in ("welcome.py", "test_welcome.py"):
        (welcome_dir / name).write_text(body)
    elif body.startswith(FORGOT):
        pass  # welcome.pyの付け忘れは、下で子のPythonに走らせて印字を照らす。
    elif body.startswith(REAL):
        result = printed(
            "result = " + body.replace("monitor(URLS, 2)", "monitor(URLS, 1)"), monitor_namespace
        )
        assert monitor_namespace["result"] == [[9], [15]], monitor_namespace["result"]
        # 刻は実時間なので照らさず、2つのページの行が1つずつ出た事を見る(2つのタスクの行の順は決まらない)。
        lines = sorted(line.split(" ", 1)[1] for line in result.splitlines())
        assert lines == ["a.example 9 bytes", "b.example 15 bytes"], result
        ran += 1
    else:
        outputs = {
            printed(body, monitor_namespace) for _ in range(3)
        }  # 仮想の時刻の順は、毎回同じ。
        assert outputs == {following}, (body, outputs, following)
        ran += 1

# 層を付けずにscheduledだけで走らせると、最初のSpendで止まる。
unhandled = ""
try:
    with contextlib.redirect_stderr(
        io.StringIO()
    ):  # doeffが先に書くtracebackは、ここでは読まない。
        exec("run(scheduled(monitor(URLS, 2)))", monitor_namespace)
except Exception as error:  # doeffのUnhandledEffectは、Pythonの例外として外へ出る。
    unhandled = f"{type(error).__name__}: {error}"
assert unhandled.startswith("UnhandledEffect: no handler found for effect: Spend"), unhandled

# test_welcome.pyは、例のdir(examples/testing_without_mocks)のinfra.pyと並べて走らせる。
(welcome_dir / "infra.py").write_text(
    (ROOT / "examples/testing_without_mocks/infra.py").read_text()
)
forgot_output = next(
    fences[i + 1][1] for i, (_, body) in enumerate(fences) if body.startswith(FORGOT)
)
script = (
    "import sys\n"
    "sys.path.insert(0, '')\n"
    "from test_welcome import fake_load_user, test_welcome\n"
    "test_welcome()\n"
    "from doeff import run\n"
    "from welcome import welcome\n"
    f"{FORGOT}\n"
)
# 読み手と同じく、例のdirで走らせる(tracebackはそのdirからのファイルの名を写す)。
child = subprocess.run(
    [sys.executable, "-I", "-c", script],
    cwd=welcome_dir,
    capture_output=True,
    text=True,
    timeout=120,
    check=False,
)
assert child.returncode != 0, child
assert child.stderr.lstrip("\n").startswith(forgot_output), (child.stderr, forgot_output)
ran += 2

# 目次: 困りごとの表の回は、連載の回の全部(AIエージェントの連載の回は、その1本目へのリンク1行で案内する)。
expected_slugs = {
    "agents",
    "llm",
    "image",
    "games",
    "events",
    "traverse",
    "time",
    "backtest",
    "color",
    "di",
    "composition",
    "memo",
    "replay",
    "durable",
    "observe",
    "operations",
    "remote",
    "tooling",
    "boundaries",
    "hy",
    "vm",
    "handlers",
    "coroutines",
    "adr",  # 2026-10-17に足した10本目「設計の記録をテストとして走らせる」。
    "commits",  # 2026-10-18に足した候補6で、2026-10-20に5本目にした「ライブラリのcommitの68%を、Claude Codeと書いた1年」。
    "types",  # 2026-10-18に足した候補7で、2026-10-20に8本目にした「公開APIの型の穴をテストで塞ぎ、黙って開かせない」。
}
all_slugs = set(re.findall(r"\(doeff-([a-z]+)\.md\)", article))
linked_slugs = expected_slugs | {"fleet"}
assert all_slugs == linked_slugs, (linked_slugs - all_slugs, all_slugs - linked_slugs)
table_text = "\n".join(line for line in article.splitlines() if line.startswith("|"))
table_slugs = set(re.findall(r"\(doeff-([a-z]+)\.md\)", table_text))
assert table_slugs == expected_slugs, (expected_slugs - table_slugs, table_slugs - expected_slugs)

# 連載の10本の一覧は、公開計画の「公開順」のdoeffの連載の表と同じ題と順にする(順を変えた時に1本目だけ古くなるのを防ぐ)。
plan_text = (SOURCE / "publication-plan.md").read_text()
plan = plan_text.split("\n### doeffの連載\n", 1)[1].split("\n### ", 1)[
    0
]  # doeffの連載の節だけを読む。
planned = re.findall(r"^\| (\d+) \| \[([^\]]+)\]\((doeff-[a-z]+\.md)\) \|", plan, re.M)
listed = re.findall(
    r"^(\d+)\. (?:\[([^\]]+)\]\((doeff-[a-z]+\.md)\)|(.+)\(この記事\))$", article, re.M
)
listed_rows = [
    (number, title or own, name or "doeff-main.md") for number, title, name, own in listed
]
assert listed_rows == planned, (listed_rows, planned)
title = re.search(r'^title: "(.+)"$', article, re.M)[1]
assert listed_rows[0][1] == title, (listed_rows[0], title)
print(
    f"main: 枠{len(fences)}個が英語の版と同じ、{ran}個の実行の印字が本文のとおり、scheduledだけでUnhandledEffect、"
    f"{len(expected_slugs)}記事の表、doeffの連載の表と同じ{len(planned)}本の一覧を確認"
)
