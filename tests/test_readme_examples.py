"""README.md の例が今の doeff でそのまま走り、書いてある通りに印字する事の検(取り組み doeff-publish・card ki-68cd4c8effcd)。

README の例は、初めての利用者が写して走らせる code で、どの検も走らせていなかったので、handler の使い方が変わっても
誰も気づかなかった: 並行の例は writer が state の handler を要るようになった後も state を入れず UnhandledEffect(Get) で
落ち、Hy の例は defhandler と do! の require が無く NameError で落ちていた(下の STALE_EXAMPLES が当時の写し)。

README の ```python と ```hy の囲みは全部この検に入る(新しい例も書いただけで入る)。囲みは 1 つずつ file に書き、
新しい interpreter で script として走らせる(`python -I -B <例>.py`・`python -I -B -m hy <例>.hy` — 利用者が写して走らせるのと
同じく、repo の木の path にも pytest の conftest にも頼らない)。終了コードだけでは足りない: 例は印字する値を print の行の
末尾の注記で言う(Python は `print(x)  # 値`・Hy は `(print x)  ; 値`)ので、標準出力の行をその注記の列と比べる。注記の
無い例は、何を確かめたかが無いので赤にする。走らせない囲み(shell の命令)は言葉の名で SHOWN_ONLY に置き、どちらにも無い
言葉の囲みは赤にする — 言葉を書き換えた囲み(```py など)が黙って検から外れないため。
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.cli.cli_child import with_settings

README = Path(__file__).resolve().parents[1] / "README.md"
# 走らせる囲みの言葉 → 例の file の拡張子と、例の path の前に置く命令。-I は環境変数の PYTHONDONTWRITEBYTECODE も読まないので、
# -B で bytecode を書かせない(書くと、起動の bytecode が checkout の下に残ったと root の conftest が赤にする)。
RUNNERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "python": (".py", (sys.executable, "-I", "-B")),
    "hy": (".hy", (sys.executable, "-I", "-B", "-m", "hy")),
}
# 走らせない囲みの言葉(写して打つ shell の命令 — 例の module や機体の道具を要る)。
SHOWN_ONLY = frozenset({"bash"})
# README が保つ入口の例の節(card ki-68cd4c8effcd が直した 2 つ)。節を消す・名を変える時は、ここも直す。
KEPT_SECTIONS = ("Quick Start", "Scheduler and Concurrency")
# 子 1 つの制限時間の、負荷で伸ばす前の値(秒)。1 本は 約 1〜2 秒(多くは doeff と hy の import)。
CHILD_DEADLINE_SECONDS = 50

_FENCE = re.compile(
    r"^```(?P<language>[^\n`]*)\n(?P<code>.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL
)
_SECTION = re.compile(r"^## (?P<title>.+?)[ \t]*$", re.MULTILINE)
# print の行の末尾の、印字する値の注記(囲みの言葉 → 注記を取る正規表現)。
_STATED_OUTPUT = {
    "python": re.compile(r"\bprint\(.*\)\s+#\s?(?P<output>.*?)\s*$"),
    "hy": re.compile(r"\(print\s.*\)\s+;\s?(?P<output>.*?)\s*$"),
}


@dataclass(frozen=True)
class Example:
    """README の囲み 1 つ。"""

    # 囲みの在る節(`## ` の見出し — 最初の見出しより前なら "")
    section: str
    # 囲みの言葉(``` の後の語)
    language: str
    # 囲みの中身
    code: str
    # 囲みの始まりの行(1 から数える)
    line: int

    @property
    def name(self) -> str:
        return f"README.md:{self.line}({self.section} の {self.language})"

    def stated_output(self) -> list[str]:
        """print の行の末尾の注記の列 — 例が印字すると言う行。"""
        pattern = _STATED_OUTPUT[self.language]
        return [
            found["output"] for line in self.code.splitlines() if (found := pattern.search(line))
        ]


def _fences(text: str) -> list[Example]:
    sections = [(found.start(), found["title"]) for found in _SECTION.finditer(text)]
    return [
        Example(
            section=next(
                (title for start, title in reversed(sections) if start < fence.start()), ""
            ),
            language=fence["language"].strip(),
            code=fence["code"],
            line=text.count("\n", 0, fence.start()) + 1,
        )
        for fence in _FENCE.finditer(text)
    ]


def _readme_fences() -> list[Example]:
    return _fences(README.read_text(encoding="utf-8"))


def _runnable() -> list[Example]:
    return [example for example in _readme_fences() if example.language in RUNNERS]


def _example_id(example: Example) -> str:
    return f"{re.sub(r'[^a-z0-9]+', '-', example.section.lower()).strip('-')}-{example.language}"


def _run(
    example: Example, directory: Path, deadline_scale_factor: float
) -> subprocess.CompletedProcess[str]:
    """例を directory の file に書き、新しい interpreter で script として走らせる(cwd も directory)。"""
    suffix, command = RUNNERS[example.language]
    script = directory / f"readme_example{suffix}"
    script.write_text(example.code, encoding="utf-8")
    return subprocess.run(
        with_settings([*command, str(script)], {}),
        cwd=directory,
        capture_output=True,
        check=False,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
    )


def _mismatch(example: Example, result: subprocess.CompletedProcess[str]) -> str | None:
    """走らせた例が落ちたか、注記と違う物を印字したかを言う文(合えば None)。"""
    if result.returncode != 0:
        return (
            f"{example.name} が終了コード {result.returncode} で落ちた — 例を今の使い方に直す\n"
            f"{result.stdout}{result.stderr}"
        )
    printed = result.stdout.splitlines()
    if printed != example.stated_output():
        return (
            f"{example.name} の印字が print の行の注記と違う — 注記 {example.stated_output()}・"
            f"印字 {printed}"
        )
    return None


@pytest.mark.parametrize("example", _runnable(), ids=_example_id)
def test_readme_example_runs_and_prints_what_it_states(
    example: Example, tmp_path: Path, deadline_scale_factor: float
) -> None:
    assert example.stated_output(), (
        f"{example.name} の print の行に、印字する値の注記が無い — 何を確かめたかが無いので、"
        "`print(x)  # 値`(Hy は `(print x)  ; 値`)の形で書く"
    )
    mismatch = _mismatch(example, _run(example, tmp_path, deadline_scale_factor))
    assert mismatch is None, mismatch


def test_readme_fences_are_run_or_shown_only() -> None:
    unknown = sorted(
        example.name
        for example in _readme_fences()
        if example.language not in RUNNERS and example.language not in SHOWN_ONLY
    )
    assert unknown == [], (
        f"README の囲みの言葉が、走らせる言葉({sorted(RUNNERS)})にも走らせない言葉({sorted(SHOWN_ONLY)})にも"
        f"無い — 言葉を直すか、走らせない shell の言葉なら SHOWN_ONLY に足す: {unknown}"
    )


def test_readme_keeps_the_entry_examples() -> None:
    sections = {example.section for example in _runnable() if example.language == "python"}
    assert set(KEPT_SECTIONS) - sections == set(), (
        f"README の入口の例の節({KEPT_SECTIONS})に、走らせる Python の例が無い"
    )


# card ki-68cd4c8effcd の前の README の 2 つの例の写し(この検が名指す事を確かめる失敗ケース)。
STALE_EXAMPLES = {
    "UnhandledEffect": Example(
        "Scheduler and Concurrency",
        "python",
        """from doeff import do, run
from doeff_core_effects import Tell
from doeff_core_effects.handlers import writer
from doeff_core_effects.scheduler import scheduled, Spawn, Wait, Gather

@do
def worker(label):
    yield Tell(f"working: {label}")
    return label

@do
def main():
    t1 = yield Spawn(worker("a"))
    t2 = yield Spawn(worker("b"))
    results = yield Gather(t1, t2)
    return results

result = run(scheduled(writer(main())))
print(result)  # ['a', 'b']
""",
        0,
    ),
    "NameError": Example(
        "Handler Composition",
        "hy",
        """(import doeff [Ask])

(defhandler ask-env
  (Ask [key]
    (resume (get {"greeting" "hello"} key))))

(ask-env
  (do!
    (<- greeting (Ask "greeting"))
    greeting))
""",
        0,
    ),
}


@pytest.mark.parametrize("error", sorted(STALE_EXAMPLES))
def test_check_names_the_stale_examples(
    error: str, tmp_path: Path, deadline_scale_factor: float
) -> None:
    example = STALE_EXAMPLES[error]
    mismatch = _mismatch(example, _run(example, tmp_path, deadline_scale_factor))
    assert mismatch is not None
    assert "落ちた" in mismatch
    assert error in mismatch


def test_check_names_an_example_that_prints_otherwise(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    example = Example("Quick Start", "python", "print(1 + 1)  # 3\n", 0)
    mismatch = _mismatch(example, _run(example, tmp_path, deadline_scale_factor))
    assert mismatch == f"{example.name} の印字が print の行の注記と違う — 注記 ['3']・印字 ['2']"


def test_fences_are_read_with_their_section_and_stated_output() -> None:
    text = """# title

```bash
pip install doeff
```

## Quick Start

```python
x = 1
print(x)  # 1
print("a # b")  # a # b
```

## Hy

```hy
(print (+ 1 1))  ; 2
(print "not stated")
```
"""
    fences = _fences(text)
    assert [(example.section, example.language, example.line) for example in fences] == [
        ("", "bash", 3),
        ("Quick Start", "python", 9),
        ("Hy", "hy", 17),
    ]
    assert fences[1].stated_output() == ["1", "a # b"]
    assert fences[2].stated_output() == ["2"]
