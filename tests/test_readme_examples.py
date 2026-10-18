"""README.md の例が今の doeff でそのまま走り、書いてある通りに印字する事の検(取り組み doeff-publish・card ki-68cd4c8effcd)。

README の例は、初めての利用者が写して走らせる code で、どの検も走らせていなかったので、handler の使い方が変わっても
誰も気づかなかった: 並行の例は writer が state の handler を要るようになった後も state を入れず UnhandledEffect(Get) で
落ち、Hy の例は defhandler と do! の require が無く NameError で落ちていた(下の STALE_EXAMPLES が当時の写し)。

README の囲みの読み方・走らせ方・注記との比べ方は tools/readme_examples.py の 1 点に在り、出す前と出した後の確かめの道具
tools/verify_release_install.py(核の 5 個を入れた新しい venv で 5 個の README を走らせる — card ki-7746480f388d)と共に使う。
この検は根の README.md の例を開発の環境で走らせ(囲みは全部入る — 新しい例も書いただけで入る)、核の 5 個の README の
囲みが約束(言葉・`file=`・`excerpt`・`run`・注記)に合う事を、走らせずに読む。核の 4 個の README の例を走らせるのは
道具の方 — 例は利用者が新しく入れた環境の様子を言い、この開発の環境には他の部品と pytest の plugin(doeff-adr の Hy の検の
集め手など)も入っているため。
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.cli.cli_child import with_settings

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
# 核の 5 個の README(区切り ms-680daa3cc6a7 の範囲 — tools/verify_release_install.py の CORE と同じ並び)。
CORE_READMES = {
    "doeff": README,
    "doeff-vm": ROOT / "packages" / "doeff-vm" / "README.md",
    "doeff-core-effects": ROOT / "packages" / "doeff-core-effects" / "README.md",
    "doeff-hy": ROOT / "packages" / "doeff-hy" / "README.md",
    "doeff-hy-pytest": ROOT / "packages" / "doeff-hy-pytest" / "README.md",
}
# README が保つ入口の例の節(頭の 2 つ = card ki-889cc73b231a・他の書き方との比べの Before / After = card ki-94411a991047・
# 並行 = card ki-68cd4c8effcd・最初の例を IO でない effect にし、IO の差し替えを使い方の 1 つの節へ移した =
# 依頼 lt-NSPA2D1VRJ9826Q7AY2X1ZTJMM)。節を消す・名を変える時は、ここも直す。
KEPT_SECTIONS = (
    "A 30-second example",
    "Swap a handler: fakes in tests, real I/O in production",
    "One handler, every effect",
    "Before and after: fakes in a test",
    "Scheduler and Concurrency",
)
# 子 1 つの制限時間の、負荷で伸ばす前の値(秒)。1 本は 約 1〜2 秒(多くは doeff と hy の import)。
CHILD_DEADLINE_SECONDS = 50


def _load(name: str) -> ModuleType:
    """tools/ の module を読む(tools/ は package ではなく、道具は workspace の外の interpreter で script として走る)。

    sys.modules に名で置く — tools/verify_release_install.py は隣の readme_examples を名で import するので、先に読んで置く。
    """
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


examples = _load("readme_examples")
release = _load("verify_release_install")
Example = examples.Example


def _readme_fences() -> list[Example]:
    """根の README の囲み(検の名の README は repo の根からの path)。"""
    return examples.fences(README.read_text(encoding="utf-8"), "README.md")


def _runnable() -> list[Example]:
    """根の README の、走らせて注記と比べる囲み。"""
    return [example for example in _readme_fences() if example.runs()]


def _example_id(example: Example) -> str:
    """parametrize の id — 節と言葉で、落ちた検がどの例かを読めるようにする。"""
    return f"{re.sub(r'[^a-z0-9]+', '-', example.section.lower()).strip('-')}-{example.language}"


def _run(
    example: Example, directory: Path, deadline_scale_factor: float
) -> subprocess.CompletedProcess[str]:
    """例を directory の file に書き、開発の環境の新しい interpreter で走らせる(cwd も directory)。"""
    return examples.run(
        example,
        directory,
        Path(sys.executable),
        CHILD_DEADLINE_SECONDS * deadline_scale_factor,
        wrap=lambda command: with_settings(command, {}),
    )


@pytest.mark.parametrize("example", _runnable(), ids=_example_id)
def test_readme_example_runs_and_prints_what_it_states(
    example: Example, tmp_path: Path, deadline_scale_factor: float
) -> None:
    assert example.problems() == []
    # 前の囲みが書く file(`file=` の module など)を、読み手と同じく先に置く。
    for earlier in _readme_fences():
        if earlier.line < example.line and earlier.written_as() is not None:
            examples.write(earlier, tmp_path)
    mismatch = examples.mismatch(example, _run(example, tmp_path, deadline_scale_factor))
    assert mismatch is None, mismatch


@pytest.mark.parametrize("package", sorted(CORE_READMES))
def test_core_readme_fences_keep_the_rules(package: str) -> None:
    path = CORE_READMES[package]
    fences = examples.fences(path.read_text(encoding="utf-8"), str(path.relative_to(ROOT)))
    problems = [problem for example in fences for problem in example.problems()]
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("package", sorted(CORE_READMES))
def test_core_readme_has_an_example_that_runs(package: str) -> None:
    # 全部を excerpt にした README は、確かめの道具で何も走らず、確かめた事が無い(道具も赤にする)。
    fences = examples.fences(CORE_READMES[package].read_text(encoding="utf-8"), package)
    assert any(example.runs() for example in fences), f"{package} の README に、走らせる例が無い"


def test_core_readmes_are_the_release_core() -> None:
    assert tuple(CORE_READMES) == release.CORE


def test_readme_keeps_the_entry_examples() -> None:
    sections = {example.section for example in _runnable() if example.language == "python"}
    assert set(KEPT_SECTIONS) - sections == set(), (
        f"README の入口の例の節({KEPT_SECTIONS})に、走らせる Python の例が無い"
    )


# card ki-68cd4c8effcd の前の README の 2 つの例の写し(この検が名指す事を確かめる失敗ケース)。
STALE_EXAMPLES = {
    "UnhandledEffect": Example(
        "README.md",
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
        "README.md",
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
    mismatch = examples.mismatch(example, _run(example, tmp_path, deadline_scale_factor))
    assert mismatch is not None
    assert "落ちた" in mismatch
    assert error in mismatch


def test_check_names_an_example_that_prints_otherwise(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    example = Example("README.md", "Quick Start", "python", "print(1 + 1)  # 3\n", 0)
    mismatch = examples.mismatch(example, _run(example, tmp_path, deadline_scale_factor))
    assert mismatch == f"{example.name} の印字が print の行の注記と違う — 注記 ['3']・印字 ['2']"


def test_fences_are_read_with_their_section_words_and_stated_output() -> None:
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

```hy file=greeting.hy
(print (+ 1 1))  ; 2
(print "not stated")
```

```bash run
# a comment line states nothing
pytest   # 2 passed
```
"""
    fences = examples.fences(text, "README.md")
    assert [
        (example.section, example.language, example.words, example.line) for example in fences
    ] == [
        ("", "bash", (), 3),
        ("Quick Start", "python", (), 9),
        ("Hy", "hy", ("file=greeting.hy",), 17),
        ("Hy", "bash", ("run",), 22),
    ]
    assert fences[1].stated_output() == ["1", "a # b"]
    assert fences[2].stated_output() == ["2"]
    assert fences[2].file == "greeting.hy"
    assert fences[3].stated_output() == ["2 passed"]
    assert [example.runs() for example in fences] == [False, True, True, True]


def _fence(info: str, code: str) -> Example:
    return examples.fences(f"## Rules\n\n```{info}\n{code}```\n", "README.md")[0]


@pytest.mark.parametrize(
    ("info", "code", "named"),
    [
        ("py", "print(1)  # 1\n", "にも無い"),
        ("python fragment", "print(1)  # 1\n", "知らない語 ['fragment']"),
        ("python run", "print(1)  # 1\n", "bash の囲みだけ"),
        ("python excerpt file=a.py", "x = 1\n", "file= とも run とも書けない"),
        ("python file=../a.py", "x = 1\n", "dir を含まない名"),
        ("python", "print(1)\n", "注記が無い"),
        ("bash run", "pytest\n", "注記が無い"),
        ("python continues", "print(1)  # 1\n", "続く先の python の囲み"),
        ("python continues file=a.py", "print(1)  # 1\n", "continues は python か hy の囲みに書き"),
        ("bash continues", "pip install doeff\n", "continues は python か hy の囲みに書き"),
    ],
)
def test_rules_name_a_fence_that_breaks_them(info: str, code: str, named: str) -> None:
    problems = _fence(info, code).problems()
    assert any(named in problem for problem in problems), problems


def test_a_continuing_fence_is_the_fence_above_it_with_its_own_lines_added() -> None:
    # 読み手は続きの囲みを、上の例の file に書き足して走らせる — 間の bash の囲みと、別の言葉の囲みは飛ばす。
    text = """## First

```python
x = 20
print(x)  # 20
```

```bash
pip install doeff
```

## Next

```hy
(print 1)  ; 1
```

```python continues
print(x + 1)  # 21
```

```python continues
print(x + 2)  # 22
```
"""
    first, _shown, _hy, second, third = examples.fences(text, "README.md")
    assert (second.continues_from, third.continues_from) == (first.line, second.line)
    assert second.code == "x = 20\nprint(x)  # 20\nprint(x + 1)  # 21\n"
    assert third.stated_output() == ["20", "21", "22"]
    assert [example.problems() for example in (second, third)] == [[], []]


@pytest.mark.parametrize("above", ["python excerpt", "python file=helper.py"])
def test_a_fence_does_not_continue_an_excerpt_or_a_module(above: str) -> None:
    # excerpt は走らない断片で、file= は後の囲みが import する module — どちらも書き足して走らせる script ではない。
    text = f"## Rules\n\n```{above}\nx = 1\n```\n\n```python continues\nprint(x)  # 1\n```\n"
    continuing = examples.fences(text, "README.md")[1]
    assert continuing.continues_from is None
    assert any("続く先の python の囲み" in problem for problem in continuing.problems())


@pytest.mark.parametrize(("stated", "failed"), [("21", []), ("20", ["README.md:8"])])
def test_a_continuing_fence_runs_as_one_script_with_the_fence_above(
    stated: str, failed: list[str], tmp_path: Path, deadline_scale_factor: float
) -> None:
    text = f"""## Steps

```python
x = 20
print(x)  # 20
```

```python continues
print(x + 1)  # {stated}
```
"""
    failures = examples.run_in_order(
        examples.fences(text, "README.md"),
        tmp_path,
        Path(sys.executable),
        CHILD_DEADLINE_SECONDS * deadline_scale_factor,
    )
    assert [failure.split("(")[0] for failure in failures] == failed


@pytest.mark.parametrize(
    ("stated", "failed"), [("42", []), ("43", ["README.md:12", "README.md:18"])]
)
def test_written_files_are_imported_by_the_fences_after_them(
    stated: str, failed: list[str], tmp_path: Path, deadline_scale_factor: float
) -> None:
    # 43 と書けば、走らせた 2 つ(main.py と bash)だけが落ちる — 走らせずに通す囲みが無い事の確かめ。
    text = f"""## Files

```python file=helper.py
def double(n):
    return n * 2
```

```python excerpt
undefined_name(1)
```

```python file=main.py
from helper import double

print(double(21))  # {stated}
```

```bash run
echo "ran in $(basename "$PWD")"   # ran in
python main.py   # {stated}
```
"""
    failures = examples.run_in_order(
        examples.fences(text, "README.md"),
        tmp_path,
        Path(sys.executable),
        CHILD_DEADLINE_SECONDS * deadline_scale_factor,
    )
    assert [failure.split("(")[0] for failure in failures] == failed
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "helper.py",
        "main.py",
        "readme_example_18.sh",
    ]


def test_shell_check_names_stated_text_missing_from_the_output(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    example = _fence("bash run", "echo hello   # goodbye\n")
    mismatch = examples.mismatch(
        example,
        examples.run(
            example, tmp_path, Path(sys.executable), CHILD_DEADLINE_SECONDS * deadline_scale_factor
        ),
    )
    assert mismatch == f"{example.name} の出力に、注記の文 ['goodbye'] が無い — 出力\nhello\n"


def test_release_check_installs_only_third_party_dependencies_from_pypi() -> None:
    # 核の 5 個は手元の wheel から入れるので、PyPI から入れる依存の列に核の名(綴りの揺れも)と extra の依存を出さない。
    assert release.dependency_requirements(
        [
            "doeff-vm>=0.2.0",
            "doeff_core_effects",
            "Doeff.Hy",
            "doeff-hy-pytest",
            "hy>=1.3.0,<=1.3.1",
            "frozendict>=2.4.0",
            'aiohttp>=3.14; extra == "http-server"',
            'tomli>=2; python_version < "3.11"',
            "hy>=1.3.0,<=1.3.1",
        ]
    ) == ["frozendict>=2.4.0", "hy>=1.3.0,<=1.3.1", 'tomli>=2; python_version < "3.11"']
