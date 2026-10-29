"""核の 4 個だけを入れた新しい venv で、README の Python の例と試しの命令 `python -m doeff demo` と LLM の agent の例が通り、Hy と
HTTP の extra が要る使い方が足す extra の名を言う事を確かめる(取り組み doeff-publish の区切り 8・card ki-d6b421afac60)。

`pip install doeff` は核の 4 個(doeff・doeff-vm・doeff-core-effects・frozendict)だけを入れ、Hy の言語は extra hy・HTTP の client の
handler は extra http で足す。tests/test_core_install_extras.py は pyproject.toml の宣言を読み、tests/test_missing_extra_hints.py は
開発の環境で Hy の import を塞いで文を読む — 開発の環境には Hy も httpx も検の道具も入っているので、核だけの入れ方が壊れても
どちらも気づけない。この道具は本物の install で確かめる:

    uv run --no-project python tools/verify_core_install.py                  # 既定 3.11 と 3.14+gil
    uv run --no-project python tools/verify_core_install.py --python 3.13    # --python は何度でも書ける
    uv run --no-project python tools/verify_core_install.py --pypi latest    # PyPI の最新(利用者の pip install doeff)
    uv run --no-project python tools/verify_core_install.py --pypi 0.6.0     # PyPI の 0.6.0

Python の版ごとに:

1. 新しい venv を repo の外の一時の dir に作り、この checkout の doeff・doeff-vm・doeff-core-effects を extra 無しで入れる(依存は
   uv.lock でなく各 package の宣言から解く — PyPI から入れた利用者と同じ)。
2. venv に入った配布物がちょうど核の 4 個で、doeff の 3 個はこの checkout から入った事を確かめる。
3. doeff と doeff-vm の README(wheel の metadata の本文 — PyPI の頁が見せる物)の Python の例を、README ごとに新しい dir で上から
   順に走らせ、注記と比べる(約束は tools/readme_examples.py)。Hy の囲みと、Hy の module を読む Python の囲み(`import hy` を
   書く物・README の Hy の file= の囲みが書く module を import する物)は extra hy の例なので走らせない。
4. Hy か HTTP の extra が要る使い方(EXTRA_USES)を走らせ、1 で終わり、標準エラーの最後の行が `pip install "doeff[<extra>]"` で
   終わる事を確かめる。
5. 3 の README の Python の例を、pyright(standard)と mypy --strict にかけ(venv の外で `uv tool run` が起こし、核の venv を指す)、
   import が「公開していない名」(reportPrivateImportUsage・attr-defined)と言われない事を確かめる。README の例そのものへの型の
   注記の文句は数えない。py.typed の package は `X as X` か `__all__` で再 export した名だけを公開とするので、README が
   `from doeff_core_effects import Ask` と書いても、再 export が欠けると利用者の editor が赤にする(card ki-d1628b5046d1)。
   あわせて、核だけの venv(Hy も httpx も無い)で package の頭の名が属性として在り、star import でも結ばれる事を確かめる。
6. README の入れ方の直後に案内する試しの命令 `python -m doeff demo` を走らせ、0 で終わり、同じ digest を 4 つの handler で
   走らせた答え — canned の答え・trace が見た effect の行・budget(1) が k を捨てた答え・作業の dir の頁を live_world が読んだ
   答え — をその順に印字する事を確かめる(card ki-34f672bff9f6・ki-2ccb68ed0ad7)。
7. README と docs/index.md が `pip install doeff` だけで走ると案内する LLM の agent の例(examples/llm_agent.py)を、例の 2 つの
   file だけを写した dir で走らせ、偽の model の答えを印字する事を確かめる。続けて venv に pytest を足し、その dir で検の例
   (examples/test_llm_agent.py)が通る事を確かめる。どちらも OPENAI_ の環境変数を外して走らせる(model の key を読まない)。
   pytest を足すので、核の 4 個だけを見る 2〜6 の後の最後に置く(依頼 lt-XNZW7KVT5ZHV9MKYY8NCWGN69S)。
8. 紹介の記事「Testing Python code without mocks」が `pip install doeff` で走ると書く例の dir(examples/testing_without_mocks)を
   写した dir で、script が記事の見せる行をその順に印字し、標準エラーに何も出さない事と、その dir の pytest が通る事を確かめる。
   fanout.py は、3 つの取り寄せが並行に走った(1 つずつなら掛かる 0.6 秒より短い)事も見る。7 と同じく pytest を足すので最後に
   置く(card ki-eeaca7016c66)。

CI の Python の版ごとの検(.github/workflows/python-compatibility.yml の job core-only-install)が走らせる。

--pypi は 1 で checkout の代わりに PyPI の doeff を入れ(doeff-vm は wheel だけを受ける — 利用者の機体に Rust の道具は無い)、2 で
doeff の 3 個が PyPI から入った事を、3 と 5 で入った版の README を確かめる。3 の後に、この checkout(CI では GitHub の main)の
doeff と doeff-vm の README(初めて来た人が GitHub で読む物)の Python の例を、同じ約束で入った版の venv で走らせる — wheel の
README は入った版と同じ時に書いた物なので、GitHub の README が出した版より先へ進んでも 3 は緑のまま食い違いを見ない
(card ki-fd6aea147621)。wheel の README と GitHub の README は別々に「<組>の例 N 個のうち入った版で通るのは M 個」と数え、
落ちた例の節の題と訳の 1 行を印字する。4・6・7 は走らせない — 期待(extra の文・試しの命令の答え・例の file)が checkout の物で、
出した版より新しい事が在る。8 は走らせる — 記事は例が今日の `pip install doeff` で走ると約束するので、例の file が出した版より
先へ進んだら(まだ出していない API を使ったら)赤にする。毎日の CI(.github/workflows/core-install-from-pypi.yml)が Linux・
macOS・Windows で走らせる(card ki-e7b4a1b9e9f4)。

終了コード: 全部通れば 0・確かめが落ちれば 1・道具が使えなければ(venv・install の失敗)2。標準ライブラリだけを使う。
"""

from __future__ import annotations

import argparse
import ast
import functools
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

# 同じ dir の module — script として走らせると、Python が script の dir を sys.path の頭に置く。
import readme_examples
import verify_release_install

REPO = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class FromCheckout:
    """この checkout から入れる doeff の配布物 1 つ。"""

    # 配布物の名(PEP 503 の形)
    name: str
    # repo の中の dir
    directory: Path


FROM_CHECKOUT = (
    FromCheckout("doeff", Path(".")),
    FromCheckout("doeff-vm", Path("packages/doeff-vm")),
    FromCheckout("doeff-core-effects", Path("packages/doeff-core-effects")),
)
# pip install doeff で入る物の全部。
CORE_FOUR = frozenset({*(package.name for package in FROM_CHECKOUT), "frozendict"})


@dataclass(frozen=True)
class Checkout:
    """この checkout の doeff の 3 個を組んで入れる(出す前の確かめ)。"""


@dataclass(frozen=True)
class PyPI:
    """PyPI の doeff を入れる(出した後の確かめ — 利用者が打つ `pip install doeff` と同じ)。"""

    # 名指した版(None は PyPI の最新 — 今日来た人が入れる物)
    version: str | None

    def requirement(self) -> str:
        """uv pip install に渡す要求。"""
        return "doeff" if self.version is None else f"doeff=={self.version}"


Source = Checkout | PyPI
# --pypi の値のうち、版でなく PyPI の最新を言う語。
LATEST = "latest"
# PyPI から入れる時に wheel だけを受ける物(PyO3 の拡張)。GitHub の runner には Rust の道具が入っているので、機体に合う wheel が
# 無くても source から組んで通ってしまい、Rust の無い利用者の機体で落ちる事に気づけない。
WHEEL_ONLY = "doeff-vm"
# 例を走らせる README。doeff-core-effects の README は外す — 例の 1 つ(節 Swapping a production handler for a test handler)が
# Hy で書いた handler(os_file・memory_file)を使い、README もその例に extra hy が要ると書く。
CHECKED_READMES = ("doeff", "doeff-vm")
# README の囲みのうち、extra を足さないと走らない言葉(Hy の言語は extra hy が入れる)。
NEEDS_AN_EXTRA = frozenset({"hy"})
# Python が Hy の module を読むために import する、Hy の本体の名(`import hy` が Hy の import hook を置く)。
HY_PACKAGE = "hy"
DEFAULT_PYTHONS = ("3.11", "3.14+gil")
# 型の check の道具。検める venv は核の 4 個だけに保つので、venv の外で uv tool run が起こし、--pythonpath・--python-executable で
# 核の venv を指す。
PYRIGHT = ("uv", "tool", "run", "pyright")
MYPY = ("uv", "tool", "run", "mypy")
# pyright の規則のうち、この確かめが名指す物 — 公開していない名(py.typed の package が再 export していない名)と、venv の package を
# 見つけられない事(見つけられなければ、公開の名の確かめにならずに緑になる)。README の例そのものへの文句(型の注記の無さなど)は
# この確かめの外。
PYRIGHT_RULES = frozenset({"reportPrivateImportUsage", "reportMissingImports"})
# mypy の同じ目印(mypy は規則でなく文と [コード] で言う)。
MYPY_MARKERS = ("does not explicitly export attribute", "[import-not-found]")
# 核だけの venv で、package の頭から取れる名の見本(README と入門の章の例が取る名)— Hy の module を読まずに結ばれている事の確かめ。
HEAD_NAMES = ("Ask", "Get", "Put", "Tell", "reader", "state", "writer")
MYPY_CHECKED = re.compile(r"(?:\(checked|no issues found in) (\d+) source files?")
# 子の process 1 つの制限時間(秒)。Hy の compile が無いので、どれも数秒で終わる。
CHILD_TIMEOUT_SECONDS = 120
# 3.15 の traceback が名を持つ ModuleNotFoundError の行の末尾に足す、似た module の名の後書き。
_SUGGESTION_SUFFIX = re.compile(r"\. Did you mean: '[^']*'\?$")
# 入れた venv の中で走らせ、入れた物の様子を JSON で印字する program(direct_url.json は PEP 610 — どこから入れたか)。
# README の本文は 3.14 までは metadata の payload に、3.15 からは Description の欄に在る(payload は空)。
_DESCRIBE = """
import importlib.metadata as metadata, json, sys, sysconfig

def readme(name):
    found = metadata.metadata(name)
    return found.get_payload() or found.get("Description", "")

def source(distribution):
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    editable = direct.get("dir_info", {}).get("editable", False)
    return [distribution.metadata["Name"], distribution.version, direct.get("url", ""), editable]

print(json.dumps({
    "version": sys.version.split()[0],
    "free_threaded": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
    "installed": [source(d) for d in metadata.distributions()],
    "readmes": [readme(name) for name in sys.argv[1:]],
}))
"""


@dataclass(frozen=True)
class WrittenFile:
    """extra が要る使い方を走らせる dir に置く file 1 つ。"""

    # dir からの path
    path: str
    text: str


# CLI の --program hyprog.mod.answer_program が読む Hy の program。
HY_PROGRAM_FILES = (
    WrittenFile("hyprog/__init__.py", ""),
    WrittenFile("hyprog/mod.hy", "(import doeff [Pure])\n(setv answer-program (Pure 42))\n"),
)


@dataclass(frozen=True)
class ExtraUse:
    """Hy か HTTP の extra が要る使い方 1 つ — 核の 4 個だけでは、足す extra の名を言う文で落ちる。"""

    # 印字の名
    name: str
    # venv の bin の下の命令(python か doeff)
    program: str
    # 命令の後の引数
    arguments: tuple[str, ...]
    # 足す extra(文の最後の `pip install "doeff[<extra>]"`)
    extra: str


def _python_use(name: str, statement: str, extra: str) -> ExtraUse:
    """Python の文 1 つを、checkout も cwd も sys.path に置かない interpreter で走らせる使い方。"""
    return ExtraUse(name, "python", ("-I", "-c", statement), extra)


# 文と extra の表は doeff_core_effects/_extra_hints.py。開発の環境で同じ場面を読む検は tests/test_missing_extra_hints.py と
# tests/cli/test_cli_without_hy.py(Hy は在り httpx だけが無い場面は、核の 4 個だけの venv には無い)。
EXTRA_USES = (
    _python_use("Hy で書いた module", "import doeff_core_effects.os_process", "hy"),
    _python_use("cache", "from doeff_core_effects._cache import cache", "hy"),
    _python_use(
        "HTTP の handler", "from doeff_core_effects import http_production_handler", "http"
    ),
    _python_use(
        "HTTP の handler の module",
        "from doeff_core_effects.http_handlers import http_fixture_handler",
        "http",
    ),
    _python_use("HTTP の effect", "from doeff_core_effects import HttpRequest", "http"),
    ExtraUse("doeff run --hy", "doeff", ("run", "--hy", "(import doeff [Pure]) (Pure 42)"), "hy"),
    ExtraUse(
        "doeff run の .hy の program",
        "doeff",
        ("run", "--program", "hyprog.mod.answer_program"),
        "hy",
    ),
    ExtraUse(
        "doeff run -c で Hy の module を読む",
        "doeff",
        ("run", "-c", "import doeff_core_effects.os_process\nPure(42)"),
        "hy",
    ),
)


# README の入れ方の直後に案内する試しの命令 `python -m doeff demo`(doeff/cli/demo.py)。live_world が取りに行く頁は --url で
# 作業の dir の file にする — urlopen は file の URL を HTTP と同じ道で読むので、検は network に出ない。
DEMO_ARGUMENTS = ("-I", "-m", "doeff", "demo")
DEMO_PAGE = "<h1>Hello, live</h1>"
# 決めた値で答える handler(canned)の答えの行 — 頁を読まず、決めた頁 <h1>Hello</h1> の字の数を言う。
DEMO_CANNED_ANSWER = "'14 chars'"
# trace が 2 つ目の effect を見て印字する行と、budget(1) が 2 つ目の effect で k を捨てて返す答えの行。
DEMO_TRACE_LINE = "trace: Summarize(text='<h1>Hello</h1>')"
DEMO_BUDGET_ANSWER = "'over budget at Summarize'"

# README と docs/index.md が `pip install doeff` だけで走ると案内する LLM の agent の例と、それを確かめる pytest の検の例
# (依頼 lt-XNZW7KVT5ZHV9MKYY8NCWGN69S)。repo の根からの path。
LLM_EXAMPLE_FILES = ("examples/llm_agent.py", "examples/test_llm_agent.py")
# 2 つの file を写す、作業の dir の下の dir。
LLM_EXAMPLE_DIRECTORY = "llm-agent"
LLM_EXAMPLE_SCRIPT = "llm_agent.py"
LLM_EXAMPLE_TESTS = "test_llm_agent.py"
# 例が偽の model(canned_reply)の答えで印字する行。
LLM_EXAMPLE_ANSWER = "answer: No: an existing queue already does all of it, for little cost."

# 紹介の記事「Testing Python code without mocks」の例の dir(card ki-eeaca7016c66)。記事は script の印字を囲みで見せ、残りの
# file(infra.py・試験の例)をこの dir への link で指す。repo の根からの path。
ARTICLE_EXAMPLE_DIRECTORY = Path("examples/testing_without_mocks")


@dataclass(frozen=True)
class ArticleScript:
    """記事の例の script 1 回の走らせ方と、記事が見せる印字。"""

    # dir の中の file の名
    name: str
    # 標準出力の行の全部(timed なら、最後の秒の行の前の全部)
    lines: tuple[str, ...]
    # 最後の行に掛かった秒を印字し、並行に走ったか(SEQUENTIAL_SECONDS より短いか)を見るか
    timed: bool = False
    # 子に渡す DOEFF_SCHEDULER の値(None は渡さない — 入った版の既定の scheduler。子の環境は DOEFF_* を外す)
    scheduler: str | None = None

    def label(self) -> str:
        """印字の名(読み手が打つ命令の形)。"""
        return (
            self.name
            if self.scheduler is None
            else f"{SCHEDULER_VARIABLE}={self.scheduler} {self.name}"
        )

    def environment(self, base: dict[str, str]) -> dict[str, str]:
        """子の環境 — base に、選んだ scheduler を足した物。"""
        return base if self.scheduler is None else {**base, SCHEDULER_VARIABLE: self.scheduler}


# scheduler を選ぶ環境変数(README と記事が書く切り替え)。
SCHEDULER_VARIABLE = "DOEFF_SCHEDULER"


_FANOUT_TITLES = (
    "['a.example', 'b.example', 'c.example']",
    "['A.EXAMPLE', 'B.EXAMPLE', 'C.EXAMPLE']",
)
# 記事の囲みと同じ行(開発の環境で同じ行を比べる検は tests/test_examples_run.py の ARTICLE_STDOUT)。記事は fanout.py の切り替えを
# DOEFF_SCHEDULER=rust と書くので、その選びでも走らせる。
ARTICLE_SCRIPTS = (
    ArticleScript(
        "production.py", ("SMTP -> grace@example.com: Welcome, Grace!", "Welcome, Grace!")
    ),
    ArticleScript(
        "trace.py",
        (
            "effect: LoadUser(user_id=1)",
            "effect: SendMail(to='grace@example.com', body='Welcome, Grace!')",
            "SMTP -> grace@example.com: Welcome, Grace!",
            "Welcome, Grace!",
        ),
    ),
    ArticleScript("retry.py", ("38.0",)),
    ArticleScript("record_replay.py", ("True 2 0",)),
    ArticleScript("fanout.py", _FANOUT_TITLES, timed=True),
    ArticleScript("fanout.py", _FANOUT_TITLES, timed=True, scheduler="rust"),
)
# fanout.py の 3 つの取り寄せは各 0.2 秒 — 1 つずつ走れば 0.6 秒かかる。
SEQUENTIAL_SECONDS = 0.6
ARTICLE_TIMED_LINE = re.compile(r"(\d+\.\d+) s")
# pytest -q の最後の行(失敗・skip・警告の無い形)。
PASSED_SUMMARY = re.compile(r"\d+ passed in \S+")


@dataclass(frozen=True)
class Distribution:
    """venv に入った配布物 1 つ。"""

    # 配布物の名(PEP 503 の形)
    name: str
    # 版
    version: str
    # 入れた所の URL(PEP 610 の direct_url.json — index から入れた物は "")
    url: str
    # dir を直に読む editable で入ったか(wheel の中身でなく checkout を読む)
    editable: bool


@dataclass(frozen=True)
class Installed:
    """新しい venv に入れた物の様子(venv の interpreter が JSON で答えた物を、境界で型を確かめて持つ)。"""

    # venv の interpreter
    python: Path
    # interpreter の版(例 3.11.16)
    version: str
    # free-threading の build か
    free_threaded: bool
    # 入った配布物の全部
    distributions: tuple[Distribution, ...]
    # README(metadata の本文)— CHECKED_READMES と同じ並び
    readmes: tuple[str, ...]

    def readme(self, package: str) -> str:
        """CHECKED_READMES の配布物 1 つの README。"""
        return self.readmes[CHECKED_READMES.index(package)]

    def command(self, name: str) -> Path:
        """venv の中の命令 1 つ(doeff など)— interpreter と同じ dir(Windows は Scripts・他の機体は bin)に在る。"""
        return venv_command(self.python.parent.parent, name)


def venv_command(venv: Path, name: str) -> Path:
    """venv の中の命令 1 つ(python・doeff など)の path — Windows の venv は Scripts\\<名>.exe、他の機体は bin/<名>。"""
    if os.name == "nt":
        return venv / "Scripts" / f"{name}.exe"
    return venv / "bin" / name


def print_in_utf8() -> None:
    """道具の印字(日本語)を UTF-8 で書く — Windows の pipe(CI の log)の既定は locale の符号(cp1252 など)で、日本語を書けずに落ちる。

    子の process の環境は変えない: 子は利用者の機体と同じく locale の符号で書き、subprocess の text の読みも locale の符号で読む。
    """
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")


def _uv(arguments: Sequence[str], cwd: Path) -> None:
    """uv を 1 回呼ぶ(落ちたら CalledProcessError — 道具が使えない事として終了コード 2 になる)。"""
    print(f"$ uv {' '.join(arguments)}", flush=True)
    subprocess.run(["uv", *arguments], cwd=cwd, check=True, text=True)


def _texts(value: object) -> tuple[str, ...]:
    """JSON の文の列である事を確かめて持つ(違えば、venv の interpreter の答えが壊れている)。"""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError(f"venv の interpreter の答えに、文の列でない値が在る: {value!r}")
    return tuple(value)


def _distribution(value: object) -> Distribution:
    """JSON の [名, 版, URL, editable か] を、入った配布物 1 つとして持つ(形が違えば、venv の interpreter の答えが壊れている)。"""
    match value:
        case [str(name), str(version), str(url), bool(editable)]:
            return Distribution(verify_release_install.normalize(name), version, url, editable)
        case _:
            raise TypeError(
                f"venv の interpreter の答えに、入った配布物の形でない値が在る: {value!r}"
            )


def install_arguments(source: Source) -> list[str]:
    """uv pip install の、入れる物と入れ方の引数(venv の指定の後に続ける)。"""
    match source:
        case Checkout():
            # uv は path から組んだ wheel を pyproject.toml が変わるまで使い回すので、今の checkout から組み直させる。
            refresh = [
                part for package in FROM_CHECKOUT for part in ("--refresh-package", package.name)
            ]
            paths = [str(REPO / package.directory) for package in FROM_CHECKOUT]
            # --no-sources: 部品の [tool.uv.sources] の workspace = true を読むと、uv は 3 個を editable(checkout を直に読む
            # .pth)で入れ、wheel の中身を確かめた事にならない。読まなければ、3 個は上の path から組んだ wheel で入る。
            return ["--no-sources", *refresh, *paths]
        case PyPI():
            # --refresh: 手元の cache の索引を読み直す(cache に前の最新が残っていると、今日来た人が入れる版を確かめない)。
            return ["--refresh", "--only-binary", WHEEL_ONLY, source.requirement()]


def install(python_request: str, place: Path, source: Source) -> Installed:
    """place に新しい venv を作り、source の doeff を extra 無しで入れる。"""
    venv = place / "venv"
    _uv(["venv", "--no-project", "--python", python_request, str(venv)], place)
    python = venv_command(venv, "python")
    _uv(["pip", "install", "--python", str(python), *install_arguments(source)], place)
    result = subprocess.run(
        [str(python), "-I", "-c", _DESCRIBE, *CHECKED_READMES],
        cwd=place,
        check=True,
        text=True,
        capture_output=True,
    )
    answer = json.loads(result.stdout)
    version, free_threaded = answer["version"], answer["free_threaded"]
    if not isinstance(version, str) or not isinstance(free_threaded, bool):
        raise TypeError(
            f"venv の interpreter の版の答えが壊れている: {version!r}・{free_threaded!r}"
        )
    if not isinstance(answer["installed"], list):
        raise TypeError(f"venv の interpreter の入った物の答えが列でない: {answer['installed']!r}")
    return Installed(
        python=python,
        version=version,
        free_threaded=free_threaded,
        distributions=tuple(map(_distribution, answer["installed"])),
        readmes=_texts(answer["readmes"]),
    )


def install_problems(installed: Installed, source: Source) -> list[str]:
    """入った物が核の 4 個でない事と、doeff の 3 個が source から入っていない事を言う文(合えば空)。"""
    names = {distribution.name for distribution in installed.distributions}
    extra = sorted(names - CORE_FOUR)
    missing = sorted(CORE_FOUR - names)
    problems = (
        [
            f"入った配布物が核の 4 個でない — 余計: {extra}・足りない: {missing}"
            "(Hy と HTTP の物は extra hy・http・all に置く — tests/test_core_install_extras.py)"
        ]
        if extra or missing
        else []
    )
    return problems + [
        problem
        for package in FROM_CHECKOUT
        for distribution in installed.distributions
        if distribution.name == package.name
        and (problem := _source_problem(package, distribution, source)) is not None
    ]


def _source_problem(
    package: FromCheckout, distribution: Distribution, source: Source
) -> str | None:
    """doeff の配布物 1 つが source から入っていない事を言う文(合えば None)。"""
    where = (
        f"入れた所: {distribution.url or 'index'}{'(editable)' if distribution.editable else ''}"
    )
    match source:
        case Checkout():
            if distribution.editable or not _is_directory(
                distribution.url, REPO / package.directory
            ):
                return f"{package.name} が、この checkout({REPO / package.directory})から組んだ wheel で入っていない — {where}"
            return None
        case PyPI(version=version):
            if distribution.url or distribution.editable:
                return f"{package.name} が PyPI から入っていない — {where}"
            if version is not None and distribution.version != version:
                return f"{package.name} が名指した版 {version} でない — 入った版: {distribution.version}"
            return None


def source_name(source: Source) -> str:
    """確かめの行に書く、doeff の 3 個の入れ元。"""
    match source:
        case Checkout():
            return "checkout から組んだ wheel"
        case PyPI(version=None):
            return "PyPI の最新"
        case PyPI(version=version):
            return f"PyPI の {version}"


def _is_directory(url: str, directory: Path) -> bool:
    """file の URL が directory を指すか。"""
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme == "file" and (
        Path(urllib.request.url2pathname(parsed.path)).resolve() == directory.resolve()
    )


def print_result(name: str, failure: str | None) -> None:
    """確かめ 1 つの結末を 1 行で(落ちたら、その訳を続けて)印字する。"""
    print(f"    {'ok' if failure is None else 'NG'}  {name}", flush=True)
    if failure is not None:
        print("        " + failure.replace("\n", "\n        "), flush=True)


@dataclass(frozen=True)
class Readme:
    """例を走らせる README 1 つ。"""

    # 配布物の名(CHECKED_READMES の 1 つ)
    package: str
    # 囲みの名の頭 — 落ちた事を言う文で、読み手が README を探し当てる名(例 "doeff の README"・"README.md")
    source: str
    text: str


@dataclass(frozen=True)
class ReadmeSet:
    """例を走らせる README の組 — 入った wheel の metadata の本文か、checkout(CI では GitHub の main)の file。"""

    # 印字の名(「<名> の例 N 個のうち…」の頭)
    title: str
    # 作業の dir の下の、例を走らせる dir の名
    directory: str
    # CHECKED_READMES と同じ並び
    readmes: tuple[Readme, ...]


# README の組の印字の名。
WHEEL_READMES = "入った wheel の README"
GITHUB_READMES = "GitHub の README"
# 各配布物の README の file の名 — pyproject.toml の readme が wheel の metadata の本文に入れる物(tests/test_verify_core_install.py)。
README_FILE = "README.md"


def wheel_readmes(installed: Installed) -> ReadmeSet:
    """入った wheel の metadata の README(PyPI の頁が見せる物)— 入った版と同じ時に書いた README。"""
    return ReadmeSet(
        WHEEL_READMES,
        "examples",
        tuple(
            Readme(package, f"{package} の README", installed.readme(package))
            for package in CHECKED_READMES
        ),
    )


def checkout_readmes(repo: Path = REPO) -> ReadmeSet:
    """この checkout の README(CI では GitHub の main — 初めて来た人が GitHub で読む物)。

    PyPI から入れた版で走らせると、README が出した版より先へ進んだ(まだ出していない使い方を書いた)事を名指す — wheel の
    README は入った版と同じ時に書いた物なので、それだけでは食い違いを見ない(card ki-fd6aea147621)。
    """
    directories = {package.name: package.directory for package in FROM_CHECKOUT}
    paths = [(package, directories[package] / README_FILE) for package in CHECKED_READMES]
    return ReadmeSet(
        GITHUB_READMES,
        "github-examples",
        tuple(
            Readme(package, path.as_posix(), (repo / path).read_text(encoding="utf-8"))
            for package, path in paths
        ),
    )


@dataclass(frozen=True)
class ExampleResult:
    """走らせた README の例 1 つの結末。"""

    example: readme_examples.Example
    # 落ちた事を言う文(通れば None)
    failure: str | None


@dataclass(frozen=True)
class CheckedReadme:
    """README 1 つの例を走らせた結末。"""

    # 走らせた例ごとの結末
    results: tuple[ExampleResult, ...]
    # 落ちた事を言う文の全部(落ちた例の全文と、約束に合わない囲み・走らせる例の無い README)
    failures: tuple[str, ...]


@dataclass(frozen=True)
class FailedExample:
    """入った版で落ちた README の例 1 つ。"""

    # README と行(例 "README.md:42")
    where: str
    # 囲みの在る節の題
    section: str
    # 落ちた訳の 1 行(例外の行など)
    error: str


@dataclass(frozen=True)
class ReadmeTally:
    """README の組 1 つの例を、入った版で走らせた数え。"""

    title: str
    # 走らせた例の数
    ran: int
    failed: tuple[FailedExample, ...]
    # 落ちた事を言う文の全部(落ちた例の全文と、約束に合わない囲み・走らせる例の無い README)
    failures: tuple[str, ...]

    def summary(self) -> list[str]:
        """数えの行と、落ちた例ごとの節の題と訳の 1 行。"""
        return [
            f"{self.title} の例 {self.ran} 個のうち入った版で通るのは {self.ran - len(self.failed)} 個",
            *(
                f"  NG  節「{failed.section}」({failed.where}): {failed.error}"
                for failed in self.failed
            ),
        ]


def error_line(failure: str) -> str:
    """落ちた事を言う文の、訳の 1 行 — 最後の空でない行(落ちた例は標準エラーの最後の行の例外・注記の違いは 1 行の文)。"""
    return next((line.strip() for line in reversed(failure.splitlines()) if line.strip()), "")


def check_readmes(
    readmes: ReadmeSet, installed: Installed, place: Path, timeout: float
) -> ReadmeTally:
    """README の組の Python の例を走らせ、数えを印字して返す — 核だけの利用者が写す例が動くかを見るため。"""
    environment = verify_release_install.example_environment(place)
    checked = [
        _check_readme(
            readme, installed, place / readmes.directory / readme.package, timeout, environment
        )
        for readme in readmes.readmes
    ]
    results = [result for readme in checked for result in readme.results]
    tally = ReadmeTally(
        readmes.title,
        ran=len(results),
        failed=tuple(
            FailedExample(
                f"{result.example.source}:{result.example.line}",
                result.example.section,
                error_line(result.failure),
            )
            for result in results
            if result.failure is not None
        ),
        failures=tuple(failure for readme in checked for failure in readme.failures),
    )
    for line in tally.summary():
        print(f"    {line}", flush=True)
    return tally


def _imported_modules(code: str) -> set[str]:
    """Python の囲みが import する module の、最初の名の集まり(読めない囲みは空 — 走らせて落ちた所を見せる)。"""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()
    names = set[str]()
    for node in ast.walk(tree):
        match node:
            case ast.Import(names=aliases):
                names.update(alias.name for alias in aliases)
            case ast.ImportFrom(module=str(module), level=0):
                names.add(module)
    return {name.split(".")[0] for name in names}


def hy_extra_examples(examples: Sequence[readme_examples.Example]) -> list[readme_examples.Example]:
    """README 1 つの囲みのうち、extra hy を足さないと走らない物 — Hy の言葉の囲みと、Hy の module を読む Python の囲み。

    Python は `import hy` の後でだけ .hy を読めるので、`import hy` を書く囲みも、同じ README の Hy の file= の囲みが書く
    module を import する囲みも、Hy の言語が無い核だけの venv では走らない。
    """
    hy_files = [example for example in examples if example.language in NEEDS_AN_EXTRA]
    reads_hy = {HY_PACKAGE, *(Path(example.file).stem for example in hy_files if example.file)}
    return [
        example
        for example in examples
        if example in hy_files
        or (
            example.language == "python"
            and not _imported_modules(example.code).isdisjoint(reads_hy)
        )
    ]


def _check_readme(
    readme: Readme,
    installed: Installed,
    directory: Path,
    timeout: float,
    environment: dict[str, str],
) -> CheckedReadme:
    """README 1 つの例(extra hy の要る囲みを除く)を、新しい dir で上から順に走らせ、結末を返す。"""
    fences = readme_examples.fences(readme.text, readme.source)
    set_aside = hy_extra_examples(fences)
    examples = [fence for fence in fences if fence not in set_aside]
    directory.mkdir(parents=True)
    print(f"  {readme.source} の例", flush=True)
    # 黙って検から外れないよう、extra hy の例として走らせない囲みの名を見せる。
    for fence in set_aside:
        print(f"    --  {fence.name}(extra hy の例 — 走らせない)", flush=True)
    results: list[ExampleResult] = []

    def record(example: readme_examples.Example, failure: str | None) -> None:
        results.append(ExampleResult(example, failure))
        print_result(example.name, failure)

    failures = readme_examples.run_in_order(
        examples, directory, installed.python, timeout, environment=environment, on_result=record
    )
    # 走らせる例が無い README は、確かめた事が無いので通さない(例を全部 excerpt か Hy にした時など)。
    empty = (
        []
        if any(example.runs() and not example.problems() for example in examples)
        else [f"{readme.source} に、核の 4 個だけで走らせる例が無い — 確かめた事が無い"]
    )
    for missing in empty:
        print_result(readme.source, missing)
    return CheckedReadme(tuple(results), (*failures, *empty))


def type_checked_examples(installed: Installed, package: str) -> list[readme_examples.Example]:
    """README 1 つの囲みのうち、核だけの venv で型の check にかける Python の囲み(extra hy の例と、書かない excerpt は除く)。

    `file=` で書く module の囲みも含める — 後の例がそれを import するので、無いと「見つからない」になる。
    """
    fences = readme_examples.fences(installed.readme(package), f"{package} の README")
    set_aside = hy_extra_examples(fences)
    return [
        fence
        for fence in fences
        if fence not in set_aside and fence.language == "python" and fence.written_as() is not None
    ]


def pyright_failures(report: object, checked: int) -> list[str]:
    """pyright --outputjson の答えのうち、公開していない名と package の見つからなさ(PYRIGHT_RULES)を言う文の列と、
    読んだ file が書いた数 checked に足りない事を言う文(合えば空)。"""
    match report:
        case {"generalDiagnostics": list(diagnostics), "summary": {"filesAnalyzed": int(analyzed)}}:
            findings = tuple(map(pyright_finding, diagnostics))
        case _:
            raise TypeError(f"pyright の答えの形が違う: {report!r}")
    named = [
        f"{Path(finding.file).name}:{finding.line + 1}: {finding.message.splitlines()[0]}  [{finding.rule}]"
        for finding in findings
        if finding.rule in PYRIGHT_RULES
    ]
    unread = (
        [
            f"pyright が読んだ file が {analyzed} 個で、書いた {checked} 個と違う — 型の check が例を読んでいない"
        ]
        if analyzed != checked
        else []
    )
    return [*named, *unread]


@dataclass(frozen=True)
class PyrightFinding:
    """pyright の所見 1 つ(--outputjson の generalDiagnostics の 1 要素)。"""

    # 規則の名(構文の誤りなど、規則の無い所見は "")
    rule: str
    message: str
    file: str
    # 0 から数える行
    line: int


def pyright_finding(diagnostic: object) -> PyrightFinding:
    """JSON の所見 1 つを持つ(形が違えば、pyright の答えが壊れている)。"""
    match diagnostic:
        case {"message": str(message), "file": str(file), "range": {"start": {"line": int(line)}}}:
            rule = diagnostic.get("rule")
            return PyrightFinding(rule if isinstance(rule, str) else "", message, file, line)
        case _:
            raise TypeError(f"pyright の所見の形が違う: {diagnostic!r}")


def mypy_failures(output: str, checked: int) -> list[str]:
    """mypy の出力のうち、公開していない名と package の見つからなさ(MYPY_MARKERS)を言う行と、
    読んだ file が書いた数 checked に足りない事を言う文(合えば空)。"""
    named = [line for line in output.splitlines() if any(mark in line for mark in MYPY_MARKERS)]
    found = MYPY_CHECKED.search(output)
    read = "数えられなかった" if found is None else f"{found[1]} 個"
    unread = (
        [
            f"mypy が読んだ file が {read}で、書いた {checked} 個と違う — 型の check が例を読んでいない"
        ]
        if found is None or int(found[1]) != checked
        else []
    )
    return [*named, *unread]


def run_checker(
    command: Sequence[str], directory: Path, timeout: float
) -> subprocess.CompletedProcess[str]:
    """型の check の道具を 1 回起こす。誤りを見つけた時の終了コード 1 までは普通 — それ以外は道具が使えない事として落とす。"""
    result = subprocess.run(
        command, cwd=directory, capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode not in (0, 1):
        raise subprocess.CalledProcessError(
            result.returncode, list(command), result.stdout, result.stderr
        )
    return result


def write_example(example: readme_examples.Example, directory: Path) -> Path:
    """README の囲み 1 つを dir の下の、囲みの名の file に書く(書かない囲みは渡さない)。"""
    name = example.written_as()
    if name is None:
        raise ValueError(f"{example.name} は file に書かない囲み")
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(example.code, encoding="utf-8")
    return path


def _check_types(package: str, installed: Installed, directory: Path, timeout: float) -> list[str]:
    """README 1 つの例を dir に書き、pyright(standard)と mypy --strict にかけて、公開していない名の誤りを言う文の列を返す。"""
    paths = [
        write_example(example, directory) for example in type_checked_examples(installed, package)
    ]
    config = directory / "pyrightconfig.json"
    config.write_text(json.dumps({"typeCheckingMode": "standard"}), encoding="utf-8")
    pyright = run_checker(
        [
            *PYRIGHT,
            "--project",
            str(config),
            "--pythonpath",
            str(installed.python),
            "--outputjson",
            *map(str, paths),
        ],
        directory,
        timeout,
    )
    mypy = run_checker(
        [
            *MYPY,
            "--strict",
            "--python-executable",
            str(installed.python),
            "--follow-imports=silent",
            "--cache-dir",
            str(directory / ".mypy_cache"),
            *map(str, paths),
        ],
        directory,
        timeout,
    )
    results = (
        ("pyright(standard)", pyright_failures(json.loads(pyright.stdout), len(paths))),
        ("mypy --strict", mypy_failures(mypy.stdout, len(paths))),
    )
    for checker, found in results:
        print_result(
            f"{package} の README の例の import を {checker} が公開の名と読む",
            "\n".join(found) or None,
        )
    return [failure for _, found in results for failure in found]


def check_head_names(installed: Installed, timeout: float) -> str | None:
    """核だけの venv(Hy も httpx も無い)で、package の頭の名が属性として在り、star import でも結ばれる事を確かめ、
    欠けを言う文を返す(合えば None)— 型の check に公開した名を、Hy の module を読まずに結んでいる事。"""
    statement = (
        "import doeff_core_effects as package\n"
        "namespace = {}\n"
        "exec('from doeff_core_effects import *', namespace)\n"
        f"names = {HEAD_NAMES!r}\n"
        "missing = [n for n in names if not hasattr(package, n) or n not in namespace]\n"
        "assert not missing, missing\n"
    )
    try:
        result = subprocess.run(
            [str(installed.python), "-I", "-c", statement],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        failure = f"package の頭の名の確かめが {timeout} 秒で終わらなかった"
    else:
        failure = (
            None
            if result.returncode == 0
            else f"核だけの venv で、package の頭の名 {HEAD_NAMES} が揃わない\n{result.stderr}"
        )
    print_result("核だけの venv で package の頭の名が属性と star import に在る", failure)
    return failure


def check_public_names(installed: Installed, place: Path, timeout: float) -> list[str]:
    """CHECKED_READMES の例が package の頭から取る名を、型の check(pyright・mypy --strict)が公開の名と読む事と、その名が核だけの
    venv で結ばれている事を確かめ、落ちた事を言う文の列を返す — README を写した利用者の editor に「公開していない名」が出ないため。"""
    print("  型の check が README の例の import を公開の名と読む", flush=True)
    typed = place / "typed"
    failures = [
        failure
        for package in CHECKED_READMES
        for failure in _check_types(package, installed, typed / package, timeout)
    ]
    head = check_head_names(installed, timeout)
    return [*failures, *([head] if head is not None else [])]


def extra_failure(use: ExtraUse, result: subprocess.CompletedProcess[str]) -> str | None:
    """extra が要る使い方の結末が、足す extra の名を言う文で 1 を返す形でない事を言う文(合えば None)。"""
    line = f'pip install "doeff[{use.extra}]"'
    last = (result.stderr.strip().splitlines() or [""])[-1]
    # 3.15 から traceback は名を持つ ModuleNotFoundError の行の末尾に似た module の名を足す(`. Did you mean: 'doeff_vm'?`)—
    # MissingExtraError の文の後ろに付くので、その後書きを除いて比べる。
    last = _SUGGESTION_SUFFIX.sub("", last)
    if result.returncode == 1 and last.endswith(line):
        return None
    return (
        f"{use.name} が、終了コード 1 と最後の行の {line} で落ちなかった"
        f"(終了コード {result.returncode})\n{result.stdout}{result.stderr}"
    )


def check_extra_uses(installed: Installed, place: Path, timeout: float) -> list[str]:
    """EXTRA_USES を走らせ、足す extra の名を言わなかった物を言う文の列を返す。"""
    directory = place / "extra-uses"
    for written in HY_PROGRAM_FILES:
        (directory / written.path).parent.mkdir(parents=True, exist_ok=True)
        (directory / written.path).write_text(written.text, encoding="utf-8")
    environment = {
        **verify_release_install.example_environment(place),
        "PYTHONPATH": str(directory),
    }
    print("  Hy と HTTP の extra が要る使い方", flush=True)
    return [
        failure
        for use in EXTRA_USES
        if (failure := _run_extra_use(use, installed, directory, environment, timeout)) is not None
    ]


def _run_extra_use(
    use: ExtraUse,
    installed: Installed,
    directory: Path,
    environment: dict[str, str],
    timeout: float,
) -> str | None:
    """使い方 1 つを venv の命令で走らせて結末を印字し、足す extra の名を言わなかった事を言う文を返す(言えば None)。"""
    try:
        result = subprocess.run(
            [str(installed.command(use.program)), *use.arguments],
            cwd=directory,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        failure = extra_failure(use, result)
    except subprocess.TimeoutExpired:
        failure = f"{use.name} が {timeout} 秒で終わらなかった"
    print_result(use.name, failure)
    return failure


def demo_failure(result: subprocess.CompletedProcess[str]) -> str | None:
    """試しの命令の結末が、0 で終わり、4 つの handler の答えの行をその順に印字した形でない事を言う文(合えば None)。"""
    expected = (
        DEMO_CANNED_ANSWER,
        DEMO_TRACE_LINE,
        DEMO_BUDGET_ANSWER,
        repr(f"{len(DEMO_PAGE)} chars"),
    )
    # 行の iterator を前から読み進め、expected の各行をその前の行より後ろに探す(間に別の行を挟んでよい)。
    printed = iter(result.stdout.splitlines())
    if result.returncode == 0 and all(line in printed for line in expected):
        return None
    return (
        f"python -m doeff demo が、終了コード 0 で 4 つの handler の答えの行 {' → '.join(expected)} をその順に"
        f"印字しなかった(終了コード {result.returncode})\n{result.stdout}{result.stderr}"
    )


def check_demo(installed: Installed, place: Path, timeout: float) -> list[str]:
    """試しの命令を核だけの venv で走らせ、落ちた事を言う文の列を返す(通れば空)— `pip install doeff` の直後に打つ命令が動くかを見るため。"""
    directory = place / "demo"
    directory.mkdir()
    page = directory / "page.html"
    page.write_text(DEMO_PAGE, encoding="utf-8")
    print("  試しの命令(README の入れ方の直後に案内する物)", flush=True)
    try:
        result = subprocess.run(
            [str(installed.python), *DEMO_ARGUMENTS, "--url", page.as_uri()],
            cwd=directory,
            env=verify_release_install.example_environment(place),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        failure = f"python -m doeff demo が {timeout} 秒で終わらなかった"
    else:
        failure = demo_failure(result)
    print_result(
        "python -m doeff demo が、同じ digest を 4 つの handler で走らせた答えをその順に言う",
        failure,
    )
    return [] if failure is None else [failure]


def llm_example_failure(result: subprocess.CompletedProcess[str]) -> str | None:
    """LLM の agent の例の結末が、0 で終わり偽の model の答えの行を印字した形でない事を言う文(合えば None)。"""
    if result.returncode == 0 and LLM_EXAMPLE_ANSWER in result.stdout.splitlines():
        return None
    return (
        f"{LLM_EXAMPLE_SCRIPT} が、終了コード 0 で答えの行 {LLM_EXAMPLE_ANSWER!r} を印字しなかった"
        f"(終了コード {result.returncode})\n{result.stdout}{result.stderr}"
    )


def llm_tests_failure(result: subprocess.CompletedProcess[str]) -> str | None:
    """検の例の pytest の結末が、0 で終わり要約の行が passed で始まる形でない事を言う文(合えば None)。"""
    lines = result.stdout.splitlines()
    if result.returncode == 0 and lines and re.match(r"\d+ passed\b", lines[-1]):
        return None
    return (
        f"pytest {LLM_EXAMPLE_TESTS} が、終了コード 0 で通らなかった(終了コード {result.returncode})\n"
        f"{result.stdout}{result.stderr}"
    )


def _llm_example_run_failure(
    installed: Installed,
    place: Path,
    arguments: Sequence[str],
    judge: Callable[[subprocess.CompletedProcess[str]], str | None],
    timeout: float,
) -> str | None:
    """LLM の agent の例の dir で venv の python を 1 回走らせ、judge が言う落ちた訳か、時間切れの訳を返す(通れば None)。

    model の key を読まない事を見るため、OPENAI_ の環境変数を外す。
    """
    environment = {
        name: value
        for name, value in verify_release_install.example_environment(place).items()
        if not name.startswith("OPENAI_")
    }
    try:
        result = subprocess.run(
            [str(installed.python), "-I", *arguments],
            cwd=place / LLM_EXAMPLE_DIRECTORY,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"python {' '.join(arguments)} が {timeout} 秒で終わらなかった"
    return judge(result)


def check_llm_example(installed: Installed, place: Path, timeout: float) -> list[str]:
    """LLM の agent の例を核だけの venv で走らせ、pytest を足して検の例を走らせ、落ちた事を言う文の列を返す(通れば空)。

    利用者と同じく、例の 2 つの file だけを置いた dir で走らせる。pytest を venv に足すので、核の 4 個だけを見る確かめの後に呼ぶ。
    """
    directory = place / LLM_EXAMPLE_DIRECTORY
    directory.mkdir()
    for name in LLM_EXAMPLE_FILES:
        shutil.copy(REPO / name, directory / Path(name).name)
    print(
        "  LLM の agent の例(README と docs/index.md が pip install doeff だけで走ると案内する物)",
        flush=True,
    )
    script_failure = _llm_example_run_failure(
        installed, place, [LLM_EXAMPLE_SCRIPT], llm_example_failure, timeout
    )
    print_result(f"{LLM_EXAMPLE_SCRIPT} が偽の model の答えを言う", script_failure)
    _uv(["pip", "install", "--python", str(installed.python), "pytest"], place)
    tests_failure = _llm_example_run_failure(
        installed, place, ["-m", "pytest", "-q", LLM_EXAMPLE_TESTS], llm_tests_failure, timeout
    )
    print_result(f"核の 4 個と pytest だけで pytest {LLM_EXAMPLE_TESTS} が通る", tests_failure)
    return [failure for failure in (script_failure, tests_failure) if failure is not None]


def article_script_failure(
    script: ArticleScript, result: subprocess.CompletedProcess[str]
) -> str | None:
    """記事の例の script 1 回の結末が、0 で終わり、標準エラーが空で、記事の見せる行をその順に印字した形でない事を言う文(合えば None)。

    timed の script は、最後の行が掛かった秒で、SEQUENTIAL_SECONDS より短い事も見る。
    """
    printed = result.stdout.splitlines()
    shown = printed[:-1] if script.timed and printed else printed
    timed = ARTICLE_TIMED_LINE.fullmatch(printed[-1]) if script.timed and printed else None
    if result.returncode != 0 or result.stderr:
        problem = f"終了コード 0 で、標準エラーに何も出さずに終わらなかった(終了コード {result.returncode})"
    elif shown != list(script.lines):
        problem = f"記事の見せる行 {list(script.lines)!r} をその順に印字しなかった"
    elif script.timed and timed is None:
        problem = "最後の行に掛かった秒(`0.20 s` の形)を印字しなかった"
    elif timed is not None and float(timed.group(1)) >= SEQUENTIAL_SECONDS:
        problem = f"取り寄せが並行に走らなかった({timed.group(1)} 秒 — 1 つずつなら {SEQUENTIAL_SECONDS} 秒)"
    else:
        return None
    return f"{script.label()} が{problem}\n{result.stdout}{result.stderr}"


def article_tests_failure(result: subprocess.CompletedProcess[str]) -> str | None:
    """記事の例の dir の pytest の結末が、0 で終わり、最後の行が失敗も skip も警告も無い passed の要約の形でない事を言う文(合えば None)。"""
    lines = result.stdout.splitlines()
    if result.returncode == 0 and lines and PASSED_SUMMARY.fullmatch(lines[-1]):
        return None
    return (
        f"{ARTICLE_EXAMPLE_DIRECTORY} の pytest が、終了コード 0 で全部通らなかった(終了コード {result.returncode})\n"
        f"{result.stdout}{result.stderr}"
    )


def _article_run_failure(
    installed: Installed,
    directory: Path,
    arguments: Sequence[str],
    environment: dict[str, str],
    judge: Callable[[subprocess.CompletedProcess[str]], str | None],
    timeout: float,
) -> str | None:
    """記事の例の dir で venv の python を 1 回走らせ、judge が言う落ちた訳か、時間切れの訳を返す(通れば None)。

    -I を付けない — 例の file は同じ dir の file を import する(trace.py が production.py を読む)ので、読み手が打つ
    `python trace.py` と同じく、script の dir が sys.path の頭に要る(-I は -P を含み、それを外す)。dir は repo の外の写しなので、
    checkout の doeff を拾わない。
    """
    try:
        result = subprocess.run(
            [str(installed.python), *arguments],
            cwd=directory,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"python {' '.join(arguments)} が {timeout} 秒で終わらなかった"
    return judge(result)


def check_article_examples(installed: Installed, place: Path, timeout: float) -> list[str]:
    """紹介の記事の例の dir を核だけの venv で走らせ、pytest を足して dir の検を走らせ、落ちた事を言う文の列を返す(通れば空)。

    読み手と同じく、例の dir を写した所で走らせる。警告も誤りとして扱う(記事は印字を囲みのまま見せる)。pytest を venv に
    足すので、核の 4 個だけを見る確かめの後に呼ぶ。
    """
    directory = place / ARTICLE_EXAMPLE_DIRECTORY.name
    shutil.copytree(
        REPO / ARTICLE_EXAMPLE_DIRECTORY,
        directory,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
    )
    print(
        f"  紹介の記事の例({ARTICLE_EXAMPLE_DIRECTORY} — 記事が pip install doeff で走ると書く物)",
        flush=True,
    )
    environment = verify_release_install.example_environment(place)
    failures: list[str] = []
    for script in ARTICLE_SCRIPTS:
        failure = _article_run_failure(
            installed,
            directory,
            ["-W", "error", script.name],
            script.environment(environment),
            functools.partial(article_script_failure, script),
            timeout,
        )
        print_result(f"{script.label()} が記事の見せる行を印字する", failure)
        failures.extend([] if failure is None else [failure])
    _uv(["pip", "install", "--python", str(installed.python), "pytest"], place)
    tests_failure = _article_run_failure(
        installed,
        directory,
        ["-m", "pytest", "-q", "-W", "error", "-p", "no:cacheprovider"],
        environment,
        article_tests_failure,
        timeout,
    )
    print_result("核の 4 個と pytest だけで、例の dir の pytest が全部通る", tests_failure)
    return [*failures, *([] if tests_failure is None else [tests_failure])]


def check_python(python_request: str, work: Path, timeout: float, source: Source) -> list[str]:
    """Python の版 1 つで、新しい venv に核だけを入れて確かめ、落ちた事を言う文の列を返す。"""
    place = work / f"py{python_request.replace('+', '-')}"
    place.mkdir(parents=True)
    print(
        f"\n== Python {python_request}: 新しい venv に doeff({source_name(source)})を extra 無しで入れる",
        flush=True,
    )
    installed = install(python_request, place, source)
    build_kind = "free-threading" if installed.free_threaded else "GIL"
    print(f"  interpreter {installed.version}({build_kind})・{installed.python}", flush=True)
    names = sorted(f"{d.name} {d.version}" for d in installed.distributions)
    print(f"  入った配布物: {'・'.join(names)}", flush=True)
    problems = install_problems(installed, source)
    print_result(
        f"入った物がちょうど核の 4 個で、doeff の 3 個は {source_name(source)}",
        "\n".join(problems) or None,
    )
    if isinstance(source, PyPI):
        # extra の文・試しの命令・LLM の例の期待は checkout の物で、出した版より新しい事が在る。wheel の README は入った版と
        # 同じ時に書いた物なので版に合う。GitHub の README と記事の例は、今日の pip install doeff で走ると読み手に約束する。
        return [
            *problems,
            *check_readmes(wheel_readmes(installed), installed, place, timeout).failures,
            # 初めて来た人が読む GitHub の README を入った版で — README が出した版より先へ進むと落ちる(card ki-fd6aea147621)。
            *check_readmes(checkout_readmes(), installed, place, timeout).failures,
            *check_public_names(installed, place, timeout),
            # venv に pytest を足すので最後に置く。
            *check_article_examples(installed, place, timeout),
        ]
    return [
        *problems,
        # checkout から組んだ wheel の README は checkout の README と同じ物なので、1 度だけ走らせる。
        *check_readmes(wheel_readmes(installed), installed, place, timeout).failures,
        *check_public_names(installed, place, timeout),
        *check_extra_uses(installed, place, timeout),
        *check_demo(installed, place, timeout),
        # venv に pytest を足すので最後に置く。
        *check_llm_example(installed, place, timeout),
        *check_article_examples(installed, place, timeout),
    ]


def parse_source(pypi: str | None) -> Source:
    """--pypi の値を入れ元にする(無ければ checkout・latest は PyPI の最新)。"""
    if pypi is None:
        return Checkout()
    return PyPI(None if pypi == LATEST else pypi)


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    """引数を読む — Python の版と、作業の dir を残すか。"""
    parser = argparse.ArgumentParser(
        description=(
            "核の 4 個だけを入れた新しい venv で、README の Python の例と、その例の import が型の check で公開の名と読まれる事と、"
            "Hy・HTTP の extra を言う文と、試しの命令 python -m doeff demo と、LLM の agent の例とその pytest の検の例と、"
            "紹介の記事の例を確かめる。"
        ),
    )
    parser.add_argument(
        "--python",
        action="append",
        dest="pythons",
        metavar="REQUEST",
        help=f"uv に渡す Python の指定(何度でも書ける・既定 {' と '.join(DEFAULT_PYTHONS)})",
    )
    parser.add_argument(
        "--pypi",
        metavar="VERSION",
        help=(
            f"checkout の代わりに PyPI の doeff の版を入れる({LATEST} は最新)— 入った版の README の例と型の check と、"
            "この checkout(GitHub)の README の例と、紹介の記事の例だけを確かめる"
        ),
    )
    parser.add_argument("--keep", action="store_true", help="終わった後も作業の dir を消さない")
    parser.add_argument(
        "--timeout",
        type=float,
        default=CHILD_TIMEOUT_SECONDS,
        help="子の process 1 つの制限時間(秒・既定 %(default)s)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """入口 — Python の版ごとに確かめ、結末を印字して終了コードを返す。"""
    print_in_utf8()
    arguments = _parse(argv)
    pythons = tuple(arguments.pythons or DEFAULT_PYTHONS)
    source = parse_source(arguments.pypi)
    if shutil.which("uv") is None:
        print("uv が PATH に無い — https://docs.astral.sh/uv/ の手順で入れる", file=sys.stderr)
        return 2
    # repo の外(uv が repo の pyproject.toml の [tool.uv] を読まず、例の import が checkout の doeff を拾わない)。
    work = Path(tempfile.mkdtemp(prefix="doeff-core-install-check-"))
    print(f"作業の dir: {work}", flush=True)
    try:
        results = {
            python: check_python(python, work, arguments.timeout, source) for python in pythons
        }
    except subprocess.CalledProcessError as failed:
        print(
            f"\nvenv か install が落ちた(確かめまでは進んでいない・終了コード {failed.returncode}): "
            f"{' '.join(map(str, failed.cmd))}",
            file=sys.stderr,
        )
        if failed.stderr:
            print(failed.stderr, file=sys.stderr)
        return 2
    finally:
        if arguments.keep:
            print(f"\n作業の dir を残した: {work}", flush=True)
        else:
            shutil.rmtree(work, ignore_errors=True)
    print("\n== 結末", flush=True)
    for python, failures in results.items():
        print(
            f"  Python {python}: {'通った' if not failures else f'NG {len(failures)} 個'}",
            flush=True,
        )
    passed = not any(results.values())
    print("\n全部通った" if passed else "\n落ちた所が在る — 上の NG の行を読む", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
