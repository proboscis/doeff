"""README の例(``` の囲み)を読み、走らせ、印字を注記と比べる 1 点(取り組み doeff-publish・card ki-68cd4c8effcd・ki-7746480f388d)。

使う所は 2 つ: repo の検 tests/test_readme_examples.py(根の README を開発の環境で走らせ、核の 5 個の README の囲みが下の約束に
合う事を読む)と、出す前と出した後の確かめの道具 tools/verify_release_install.py(核の 5 個を入れた新しい venv で、5 個の
README の例を走らせる)。どちらも同じ約束で README を読む:

- 囲みの言葉(``` の後の最初の語)が扱いを決める。python と hy は file に書いて新しい interpreter で script として走らせ、
  print の行の末尾の注記(Python は `print(x)  # 値`・Hy は `(print x)  ; 値`)の列と標準出力の行の列を比べる。終了コードだけ
  では足りないので、注記の無い例は何を確かめたかが無いとして赤にする。bash・text・toml は見せるだけ。どれでもない言葉は赤
  (```py などに書き換えた囲みが黙って検から外れないため)。
- 言葉の後の語で扱いを足す(GitHub も PyPI も最初の語だけを言葉として読み、後の語は描かない):
  - `file=<名>`: 囲みを例の dir の <名> の file に書く(後の囲みが import する module・conftest.py など)。注記の在る print を
    持てば、書いた後に script として走らせる。
  - `excerpt`: 大きな file の一部(書いていない名を使う)。走らせない。
  - `run`(bash だけ): 囲みを `bash -e` で走らせる。命令の行の末尾の注記(`命令  # 文`)の文が、出力(標準出力と標準エラー)の
    どこかに在る事を確かめる — 道具の出力は時刻や版も印字し、行ごとには比べられないため。
- 1 つの README の囲みは、上から順に 1 つの dir で扱う(読み手が書いてある順に file を置いて走らせるのと同じ)。

interpreter は -E -s -B で起こす: 環境変数の PYTHON* と利用者の site を読まず(repo の木の path にも pytest の conftest にも
頼らない)、bytecode を書かない(書くと、起動の bytecode が checkout の下に残ったと root の conftest が赤にする)。-I と違い
script の dir は sys.path に入るので、`file=` で隣に書いた module を import できる。

標準ライブラリだけを使う(tools/verify_release_install.py は workspace を同期せずに走る)。
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Runner:
    """走らせる言葉の囲みを、どの file に書いてどう起こすか。"""

    # 例の file の拡張子
    suffix: str
    # interpreter の後に置く引数
    arguments: tuple[str, ...]


# 走らせる言葉 → 走らせ方。
RUNNERS = {
    "python": Runner(".py", ("-E", "-s", "-B")),
    "hy": Runner(".hy", ("-E", "-s", "-B", "-m", "hy")),
}
# `run` の語が在れば走らせる shell の言葉。
SHELL = "bash"
# 走らせない言葉(写して打つ shell の命令・図・設定の断片)。
SHOWN_ONLY = frozenset({SHELL, "text", "toml"})
# 言葉の後に書ける語(`file=<名>` の他)。
EXCERPT = "excerpt"
RUN = "run"

_FENCE = re.compile(r"^```(?P<info>[^\n`]*)\n(?P<code>.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)
_SECTION = re.compile(r"^## (?P<title>.+?)[ \t]*$", re.MULTILINE)
# print の行の末尾の、印字する値の注記(囲みの言葉 → 注記を取る正規表現)。
_STATED_OUTPUT = {
    "python": re.compile(r"\bprint\(.*\)\s+#\s?(?P<output>.*?)\s*$"),
    "hy": re.compile(r"\(print\s.*\)\s+;\s?(?P<output>.*?)\s*$"),
    # 命令の行(# で始まらない)の、最初の「空白 # 空白」から後。
    SHELL: re.compile(r"^\s*[^#\s][^#]*?\s+#\s(?P<output>.*?)\s*$"),
}
_FILE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


@dataclass(frozen=True)
class Example:
    """README の囲み 1 つ。"""

    # どの README か(印字の名に使う — 例 "README.md"・"doeff-hy の README")
    source: str
    # 囲みの在る節(`## ` の見出し — 最初の見出しより前なら "")
    section: str
    # 囲みの言葉(``` の後の最初の語)
    language: str
    # 囲みの中身
    code: str
    # 囲みの始まりの行(1 から数える)
    line: int
    # 言葉の後の語
    words: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        """落ちた事を言う文で、読み手が README の囲みを探し当てるための名。"""
        return f"{self.source}:{self.line}({self.section} の {self.language})"

    @property
    def file(self) -> str | None:
        """`file=<名>` の名(無ければ None)。"""
        return next((word[len("file=") :] for word in self.words if word.startswith("file=")), None)

    def stated_output(self) -> list[str]:
        """行の末尾の注記の列 — 例が印字すると言う物。"""
        pattern = _STATED_OUTPUT.get(self.language)
        if pattern is None:
            return []
        return [
            found["output"] for line in self.code.splitlines() if (found := pattern.search(line))
        ]

    def written_as(self) -> str | None:
        """例の dir に書く file の名(書かない囲みは None)。"""
        if self.language in RUNNERS and EXCERPT not in self.words:
            return self.file or f"readme_example_{self.line}{RUNNERS[self.language].suffix}"
        if self.language == SHELL and RUN in self.words:
            return f"readme_example_{self.line}.sh"
        return None

    def runs(self) -> bool:
        """走らせて注記と比べる囲みか。"""
        if self.written_as() is None:
            return False
        return self.file is None or bool(self.stated_output())

    def problems(self) -> list[str]:
        """約束に合わない所(合えば空)。"""
        return list(self._problems())

    def _problems(self) -> Iterator[str]:
        """problems の中身 — 約束の 1 条ごとに、合わなければその条を言う文を出す。"""
        if self.language not in RUNNERS and self.language not in SHOWN_ONLY:
            yield (
                f"{self.name} の言葉が、走らせる言葉({sorted(RUNNERS)})にも走らせない言葉"
                f"({sorted(SHOWN_ONLY)})にも無い — 言葉を直すか、走らせない言葉なら SHOWN_ONLY に足す"
            )
        unknown = [
            word
            for word in self.words
            if word not in (EXCERPT, RUN) and not word.startswith("file=")
        ]
        if unknown:
            yield (
                f"{self.name} の言葉の後に、知らない語 {unknown} が在る"
                f"(書ける語: file=<名>・{EXCERPT}・{RUN})"
            )
        if RUN in self.words and self.language != SHELL:
            yield f"{self.name} の {RUN} は {SHELL} の囲みだけに書ける"
        if EXCERPT in self.words and (self.file is not None or RUN in self.words):
            yield f"{self.name} の {EXCERPT} は走らせない囲みなので、file= とも {RUN} とも書けない"
        if self.file is not None and (
            self.language not in RUNNERS or not _FILE_NAME.fullmatch(self.file)
        ):
            yield f"{self.name} の file={self.file} は、python か hy の囲みに、dir を含まない名で書く"
        if self.runs() and not self.stated_output():
            yield (
                f"{self.name} に、印字する値の注記が無い — 何を確かめたかが無いので、"
                "`print(x)  # 値`(Hy は `(print x)  ; 値`・bash は `命令  # 出力に在る文`)の形で書く"
            )


def _info(fence: re.Match[str]) -> tuple[str, ...]:
    """囲みの ``` の後の語の列(言葉が無ければ空の言葉 1 つ)。"""
    return tuple(fence["info"].split()) or ("",)


def fences(text: str, source: str) -> list[Example]:
    """README の本文の囲みを上から順に読む。"""
    sections = [(found.start(), found["title"]) for found in _SECTION.finditer(text)]
    return [
        Example(
            source=source,
            section=next(
                (title for start, title in reversed(sections) if start < fence.start()), ""
            ),
            language=_info(fence)[0],
            code=fence["code"],
            line=text.count("\n", 0, fence.start()) + 1,
            words=_info(fence)[1:],
        )
        for fence in _FENCE.finditer(text)
    ]


def write(example: Example, directory: Path) -> Path:
    """囲みを例の dir の file に書く(書かない囲みには使わない)。"""
    name = example.written_as()
    if name is None:
        raise ValueError(f"{example.name} は file に書かない囲み")
    path = directory / name
    path.write_text(example.code, encoding="utf-8")
    return path


def command(example: Example, script: Path, python: Path) -> list[str]:
    """書いた囲みを走らせる命令。"""
    if example.language == SHELL:
        return ["bash", "-e", script.name]
    return [str(python), *RUNNERS[example.language].arguments, script.name]


def run(
    example: Example,
    directory: Path,
    python: Path,
    timeout: float,
    *,
    environment: Mapping[str, str] | None = None,
    wrap: Callable[[list[str]], list[str]] = list,
) -> subprocess.CompletedProcess[str]:
    """囲みを directory の file に書き、cwd を directory にして走らせる。

    python の dir を PATH の頭に置く(bash の囲みの `pytest`・`python` が、その interpreter の環境の物になる)。
    environment が無ければ、この process の環境を継ぐ。wrap は命令の前に置く物(検の `env …` など)を足す。
    """
    script = write(example, directory)
    inherited = os.environ if environment is None else environment
    path = os.pathsep.join(filter(None, (str(python.parent), inherited.get("PATH"))))
    return subprocess.run(
        wrap(command(example, script, python)),
        cwd=directory,
        env={**inherited, "PATH": path},
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )


def mismatch(example: Example, result: subprocess.CompletedProcess[str]) -> str | None:
    """走らせた例が落ちたか、注記と違う物を印字したかを言う文(合えば None)。"""
    if result.returncode != 0:
        return (
            f"{example.name} が終了コード {result.returncode} で落ちた — 例を今の使い方に直す\n"
            f"{result.stdout}{result.stderr}"
        )
    if example.language == SHELL:
        output = result.stdout + result.stderr
        missing = [stated for stated in example.stated_output() if stated not in output]
        if missing:
            return f"{example.name} の出力に、注記の文 {missing} が無い — 出力\n{output}"
        return None
    printed = result.stdout.splitlines()
    if printed != example.stated_output():
        return (
            f"{example.name} の印字が print の行の注記と違う — 注記 {example.stated_output()}・"
            f"印字 {printed}"
        )
    return None


def run_in_order(
    examples: Sequence[Example],
    directory: Path,
    python: Path,
    timeout: float,
    *,
    environment: Mapping[str, str] | None = None,
    on_result: Callable[[Example, str | None], None] = lambda _example, _failure: None,
) -> list[str]:
    """1 つの README の囲みを上から順に directory で扱い、落ちた事を言う文の列を返す(全部通れば空)。

    約束に合わない囲みは走らせずに数える。on_result は走らせた囲みごとに(落ちた事を言う文か None で)呼ぶ。
    """
    return list(
        _failures(
            examples, directory, python, timeout, environment=environment, on_result=on_result
        )
    )


def _failures(
    examples: Sequence[Example],
    directory: Path,
    python: Path,
    timeout: float,
    *,
    environment: Mapping[str, str] | None,
    on_result: Callable[[Example, str | None], None],
) -> Iterator[str]:
    """run_in_order の中身 — 囲みを順に書き・走らせ、落ちた事を言う文を出す。"""
    for example in examples:
        problems = example.problems()
        if problems:
            yield from problems
            continue
        if not example.runs():
            if example.written_as() is not None:
                write(example, directory)
            continue
        try:
            failure = mismatch(
                example, run(example, directory, python, timeout, environment=environment)
            )
        except subprocess.TimeoutExpired:
            failure = f"{example.name} が {timeout} 秒で終わらなかった"
        on_result(example, failure)
        if failure is not None:
            yield failure
