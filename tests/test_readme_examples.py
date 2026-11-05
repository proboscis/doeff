"""Test that the examples in README.md run as-is on current doeff and print as documented (doeff-publish initiative).

The README examples are code that first-time users copy and run, and no test ran them, so when the way to use handlers changed,
nobody noticed: the concurrency example, even after writer came to need a state handler, did not install state and failed with UnhandledEffect(Get),
and the Hy example lacked the require of defhandler and do! and failed with NameError (STALE_EXAMPLES below are copies from that time).

How to read README fences, run them, and compare them with annotations is defined at a single point, tools/readme_examples.py, which is used together with the pre- and post-release verification tool
tools/verify_release_install.py (runs the 5 READMEs in a fresh venv with the 5 core components installed).
This test runs the examples of the root README.md in the development environment (all fences are included — a new example is included just by writing it), and reads without running
that the fences of the 5 core READMEs conform to the convention (language, `file=`, `excerpt`, `run`, annotations). Running the examples of the 4 other core READMEs is done by
the tool — the examples describe a user's freshly installed environment, and this development environment also has other components and pytest plugins (such as the Hy test
collector of doeff-adr).
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
# READMEs of the 5 core components (same order as CORE in tools/verify_release_install.py).
CORE_READMES = {
    "doeff": README,
    "doeff-vm": ROOT / "packages" / "doeff-vm" / "README.md",
    "doeff-core-effects": ROOT / "packages" / "doeff-core-effects" / "README.md",
    "doeff-hy": ROOT / "packages" / "doeff-hy" / "README.md",
    "doeff-hy-pytest": ROOT / "packages" / "doeff-hy-pytest" / "README.md",
}
# Entry example sections kept by the README (the first two, the Before / After comparison with other styles, concurrency, and the section that made the first example a non-IO effect
# and moved IO substitution into one usage section). When deleting or renaming a section, fix this too.
KEPT_SECTIONS = (
    "A 30-second example",
    "Swap a handler: fakes in tests, real I/O in production",
    "One handler, every effect",
    "Before and after: fakes in a test",
    "Scheduler and Concurrency",
)
# Time limit (seconds) for one child, before stretching under load. One takes about 1 to 2 seconds (mostly importing doeff and hy).
CHILD_DEADLINE_SECONDS = 50


def _load(name: str) -> ModuleType:
    """Load a module from tools/ (tools/ is not a package; the tools run as scripts in an interpreter outside the workspace).

    Register it in sys.modules by name — tools/verify_release_install.py imports the neighboring readme_examples by name, so load and register it first.
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
    """Fences of the root README (a README given as this test's name is a path from the repo root)."""
    return examples.fences(README.read_text(encoding="utf-8"), "README.md")


def _runnable() -> list[Example]:
    """Fences of the root README that are run and compared with annotations."""
    return [example for example in _readme_fences() if example.runs()]


def _example_id(example: Example) -> str:
    """parametrize id — by section and language, so that a failed test shows which example it was."""
    return f"{re.sub(r'[^a-z0-9]+', '-', example.section.lower()).strip('-')}-{example.language}"


def _run(
    example: Example, directory: Path, deadline_scale_factor: float
) -> subprocess.CompletedProcess[str]:
    """Write the example to a file in a directory and run it in a fresh interpreter of the development environment (cwd is also the directory)."""
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
    # Place the files written by earlier fences (`file=` modules etc.) first, as a reader would.
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
    # A README where everything is an excerpt runs nothing in the verification tool and verifies nothing (the tool also goes red).
    fences = examples.fences(CORE_READMES[package].read_text(encoding="utf-8"), package)
    assert any(example.runs() for example in fences), f"the README of {package} has no example to run"


def test_core_readmes_are_the_release_core() -> None:
    assert tuple(CORE_READMES) == release.CORE


def test_readme_keeps_the_entry_examples() -> None:
    sections = {example.section for example in _runnable() if example.language == "python"}
    assert set(KEPT_SECTIONS) - sections == set(), (
        f"the README entry example sections ({KEPT_SECTIONS}) have no Python example to run"
    )


# Copies of two README examples from before the concurrency section was added (failure cases that confirm this test names them).
STALE_EXAMPLES = {
    "UnhandledEffect": Example(
        "README.md",
        "Scheduler and Concurrency",
        "python",
        """from doeff import do, run
from doeff_core_effects import Tell
from doeff_core_effects._handlers import writer
from doeff_core_effects._scheduler import scheduled, Spawn, Wait, Gather

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
    # A reader appends the continuation fence to the file of the example above and runs it — bash fences in between and fences of other languages are skipped.
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
    # An excerpt is a fragment that is not run, and file= is a module imported by a later fence — neither is a script to be appended and run.
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
    # Writing 43 makes only the 2 that were run (main.py and bash) fail — confirming that no fence passes without being run.
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
    # The 5 core components are installed from local wheels, so the dependency list installed from PyPI must not contain core names (including spelling variants) or extra dependencies.
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
