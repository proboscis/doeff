"""核だけの入れ方の検の道具 tools/verify_core_install.py が、壊れた入れ方を名指す事の失敗ケース(card ki-d6b421afac60)。

道具は Python の版ごとに、本物の install(核の 4 個だけの新しい venv)で確かめる。道具の判じ方が黙って緑を返すと、核だけの
入れ方が壊れても道具は落ちない。ここでは判じ方を、入れた物と子の process の結末の写しで確かめる(本物の install は doeff-vm
を組むので 1 版 約 1 分かかり、開発の環境の検には入れない)。
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import get_args

import pytest
import yaml
from doeff_core_effects import extra_hints

from doeff import handler, run
from doeff.cli.demo import digest, fake_world

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "python-compatibility.yml"


def _load(name: str) -> ModuleType:
    """tools/ の module を読む(tools/ は package ではない)。道具は隣の module を名で import するので、sys.modules に名で置く。"""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


readme_examples = _load("readme_examples")
_load("verify_release_install")
tool = _load("verify_core_install")


def _from_checkout(name: str, *, editable: bool = False) -> object:
    """この checkout の dir から入った配布物の写し。"""
    directory = next(package.directory for package in tool.FROM_CHECKOUT if package.name == name)
    return tool.Distribution(name, (ROOT / directory).resolve().as_uri(), editable)


def _installed(*distributions: object) -> object:
    """入った物の判じ方だけを試すための、venv の様子の写し(README は読まない)。"""
    return tool.Installed(Path("venv/bin/python"), "3.12.14", False, distributions, ("", ""))


CORE_FROM_WHEELS = (
    _from_checkout("doeff"),
    _from_checkout("doeff-vm"),
    _from_checkout("doeff-core-effects"),
    tool.Distribution("frozendict", "", False),
)


def test_the_core_four_built_from_the_checkout_pass() -> None:
    assert tool.install_problems(_installed(*CORE_FROM_WHEELS)) == []


def test_an_install_that_brings_more_than_the_core_four_is_named() -> None:
    # 0.5 までの既定(doeff-hy と httpx を既定の依存に持つ)の入れ方の一部。
    problems = tool.install_problems(
        _installed(
            *CORE_FROM_WHEELS,
            tool.Distribution("doeff-hy", "", False),
            tool.Distribution("httpx", "", False),
        )
    )
    assert len(problems) == 1
    assert "余計: ['doeff-hy', 'httpx']・足りない: []" in problems[0]


def test_an_editable_or_index_install_of_a_checkout_package_is_named() -> None:
    # editable は checkout を直に読み(wheel の中身を確かめない)、index の物は checkout の物でない。
    problems = tool.install_problems(
        _installed(
            _from_checkout("doeff", editable=True),
            tool.Distribution("doeff-vm", "", False),
            _from_checkout("doeff-core-effects"),
            tool.Distribution("frozendict", "", False),
        )
    )
    assert [problem.split(" ")[0] for problem in problems] == ["doeff", "doeff-vm"]
    assert "(editable)" in problems[0]
    assert "入れた所: index" in problems[1]


HY_LINE = (
    "doeff_core_effects.extra_hints.MissingExtraError: No module named 'doeff_hy' — Hy modules "
    'need Hy. Install the extra hy: pip install "doeff[hy]"'
)


@pytest.mark.parametrize(
    ("returncode", "stderr", "extra", "passes"),
    [
        (1, f"Traceback (most recent call last):\n{HY_LINE}\n", "hy", True),
        (1, "Error: No module named 'doeff_hy'\n", "hy", False),
        (1, f"{HY_LINE}\n", "http", False),
        (0, f"{HY_LINE}\n", "hy", False),
        (1, f"{HY_LINE}\nException ignored in: <function _cleanup>\n", "hy", False),
    ],
    ids=["names-the-extra", "bare-module-error", "other-extra", "exit-0", "line-not-last"],
)
def test_an_extra_use_passes_only_when_its_last_line_installs_the_extra(
    returncode: int, stderr: str, extra: str, passes: bool
) -> None:
    use = tool.ExtraUse("use", "python", (), extra)
    result = subprocess.CompletedProcess(["python"], returncode, "", stderr)
    assert (tool.extra_failure(use, result) is None) is passes


def test_the_extra_uses_cover_every_extra_of_the_hint_table() -> None:
    assert {use.extra for use in tool.EXTRA_USES} == set(get_args(extra_hints.Extra))


# --- 試しの命令 python -m doeff demo が核だけの venv で動く(card ki-34f672bff9f6) -------------------------------------------
DEMO_LIVE = repr(f"{len(tool.DEMO_PAGE)} chars")
DEMO_OUTPUT = (
    f">>> run(handler(fake_world)(digest(url)))\n{tool.DEMO_FAKE_ANSWER}\n\n"
    f">>> run(handler(fake_world)(handler(live_world)(digest(url))))\n{DEMO_LIVE}\n\n"
)


@pytest.mark.parametrize(
    ("returncode", "stdout", "passes"),
    [
        (0, DEMO_OUTPUT, True),
        (1, DEMO_OUTPUT, False),
        (0, f"{tool.DEMO_FAKE_ANSWER}\n", False),
        (0, f"{DEMO_LIVE}\n", False),
        (0, f"{DEMO_LIVE}\n{tool.DEMO_FAKE_ANSWER}\n", False),
        (0, f"x {tool.DEMO_FAKE_ANSWER}\nx {DEMO_LIVE}\n", False),
    ],
    ids=["fake-then-live", "exit-1", "no-live", "no-fake", "live-first", "not-own-lines"],
)
def test_the_demo_passes_only_when_it_prints_the_fake_answer_then_the_page_it_read(
    returncode: int, stdout: str, passes: bool
) -> None:
    result = subprocess.CompletedProcess(["python"], returncode, stdout, "")
    assert (tool.demo_failure(result) is None) is passes


def test_the_demo_check_expects_the_answer_of_the_demos_fake_handler() -> None:
    # 道具は標準ライブラリだけで走るので doeff を import できず、偽の答えを写して持つ — 写しが demo の答えとずれない事。
    assert repr(run(handler(fake_world)(digest(tool.DEMO_PAGE)))) == tool.DEMO_FAKE_ANSWER


def test_the_demo_check_runs_the_command_the_readme_points_to() -> None:
    command = " ".join(("python", *tool.DEMO_ARGUMENTS[1:]))
    assert command == "python -m doeff demo"
    assert f"`{command}`" in (ROOT / "README.md").read_text(encoding="utf-8")


# --- LLM の agent の例と、その pytest の検の例が核の 4 個(と pytest)だけで通る(依頼 lt-XNZW7KVT5ZHV9MKYY8NCWGN69S) -------------
@pytest.mark.parametrize(
    ("returncode", "stdout", "passes"),
    [
        (0, f"question: q\n{tool.LLM_EXAMPLE_ANSWER}\n", True),
        (1, f"{tool.LLM_EXAMPLE_ANSWER}\n", False),
        (0, "question: q\nanswer: something else\n", False),
        (0, f"> {tool.LLM_EXAMPLE_ANSWER}\n", False),
    ],
    ids=["answer", "exit-1", "other-answer", "not-own-line"],
)
def test_the_llm_example_passes_only_when_it_prints_the_fake_models_answer(
    returncode: int, stdout: str, passes: bool
) -> None:
    result = subprocess.CompletedProcess(["python"], returncode, stdout, "")
    assert (tool.llm_example_failure(result) is None) is passes


@pytest.mark.parametrize(
    ("returncode", "stdout", "passes"),
    [
        (0, "....\n4 passed in 0.12s\n", True),
        (0, "....\n4 passed, 1 warning in 0.12s\n", True),
        (1, ".F..\n1 failed, 3 passed in 0.12s\n", False),
        (5, "\nno tests ran in 0.01s\n", False),
        (0, "", False),
        (0, "4 passed in 0.12s\nException ignored in: <function _cleanup>\n", False),
    ],
    ids=["passed", "passed-with-warning", "failed", "no-tests", "no-output", "summary-not-last"],
)
def test_the_llm_example_tests_pass_only_when_pytest_ends_with_a_passed_summary(
    returncode: int, stdout: str, passes: bool
) -> None:
    result = subprocess.CompletedProcess(["python"], returncode, stdout, "")
    assert (tool.llm_tests_failure(result) is None) is passes


def test_the_llm_example_check_expects_the_line_the_example_prints() -> None:
    # 道具は doeff を import できず、例の答えの行を写して持つ — 写しが、開発の環境で例の印字と比べる行とずれない事。
    from tests.test_examples_run import EXPECTED_LINES

    assert tool.LLM_EXAMPLE_ANSWER in EXPECTED_LINES[tool.LLM_EXAMPLE_SCRIPT]


def test_the_llm_example_check_copies_the_example_and_its_tests() -> None:
    assert [Path(name).name for name in tool.LLM_EXAMPLE_FILES] == [
        tool.LLM_EXAMPLE_SCRIPT,
        tool.LLM_EXAMPLE_TESTS,
    ]
    assert all((ROOT / name).is_file() for name in tool.LLM_EXAMPLE_FILES)


@pytest.mark.parametrize("page", ["README.md", "docs/index.md"])
def test_the_pages_that_say_the_llm_example_needs_only_doeff_link_to_it(page: str) -> None:
    # 道具が確かめる約束(pip install doeff だけで走る)を言う page が、確かめる例そのものを指している事。
    assert "examples/llm_agent.py" in (ROOT / page).read_text(encoding="utf-8")


# 根の README の「Hy の module を Python から読む」節の形(card ki-909e814cc835 が足し、核の 4 個だけの venv で落ちた)。
HY_MODULE_README = """\
## Quick Start

```python
from doeff import run  # imported
print(run)  # <function>
```

## Using a Hy module from Python

```hy file=greet.hy
(import doeff [Ask])
```

```python
import hy  # first: registers Hy's import hook
from greet import greet

print(greet)  # <function>
```

```hy
(print "hello")  ; hello
```
"""


def _set_aside(readme: str) -> list[str]:
    """README の囲みのうち、extra hy の例として走らせない物の言葉と始まりの行。"""
    examples = readme_examples.fences(readme, "README")
    return [f"{example.language}:{example.line}" for example in tool.hy_extra_examples(examples)]


def test_a_python_example_that_imports_hy_is_set_aside_with_the_hy_ones() -> None:
    # 14 行目の Python が `import hy` と Hy の module(greet)を読む物。Quick Start の Python は走らせる。
    assert _set_aside(HY_MODULE_README) == ["hy:10", "python:14", "hy:21"]


@pytest.mark.parametrize(
    ("code", "set_aside"),
    [
        ("import hy\n", True),
        ("import hy.models\n", True),
        ("from hy import read\n", True),
        ("from hy.models import Symbol\n", True),
        ("import os, hy\n", True),
        ("if True:\n    import hy\n", True),
        ("from greet import greet\n", True),
        ("import greet\n", True),
        ("from doeff import run\n", False),
        ("# import hy\nfrom doeff import run\n", False),
        ('print("import hy")\n', False),
        ("from . import hy\n", False),
        ("import hyper\n", False),
        ("import hy(\n", False),
    ],
    ids=[
        "import",
        "import-submodule",
        "from-import",
        "from-submodule",
        "among-others",
        "nested",
        "from-hy-file-module",
        "hy-file-module",
        "core-only",
        "comment",
        "string",
        "relative",
        "other-package-with-hy-prefix",
        "unreadable-code-is-run-and-fails-loudly",
    ],
)
def test_a_python_example_is_set_aside_only_when_it_reads_a_hy_module(
    code: str, set_aside: bool
) -> None:
    readme = f"```hy file=greet.hy\n(import doeff [Ask])\n```\n\n```python\n{code}```\n"
    assert ("python:5" in _set_aside(readme)) is set_aside


def test_a_python_example_reading_a_module_no_hy_file_writes_is_run() -> None:
    # greet.hy を書く Hy の囲みが無ければ、`greet` は Hy の module と言えない(`from greet import greet` は他の file が書く)。
    readme = "```python file=greet.py\ndef greet(): ...\nprint(1)  # 1\n```\n\n```python\nimport greet\n```\n"
    assert _set_aside(readme) == []


def test_examples_that_do_not_read_hy_are_left_to_run_in_the_core_only_venv() -> None:
    readme = (
        "```bash\npip install doeff\n```\n\n```python\nprint(1)  # 1\n```\n\n```toml\n[a]\n```\n"
    )
    assert _set_aside(readme) == []


@pytest.mark.parametrize("package", tool.CHECKED_READMES)
def test_every_python_example_that_imports_hy_in_a_checked_readme_is_set_aside(
    package: str,
) -> None:
    # 道具が走らせる README(根と doeff-vm)の今の本文で、`import hy` を書く Python の囲みが核だけの venv の側に残らない事。
    path = ROOT / ("README.md" if package == "doeff" else f"packages/{package}/README.md")
    examples = readme_examples.fences(path.read_text(encoding="utf-8"), package)
    set_aside = tool.hy_extra_examples(examples)
    remaining_importing_hy = [
        example.name
        for example in examples
        if example.language == "python"
        and example not in set_aside
        and re.search(r"^\s*(import hy\b|from hy\b)", example.code, re.MULTILINE)
    ]
    assert remaining_importing_hy == []


# --- 型の check が「公開していない名」を名指す(card ki-d1628b5046d1) ---------------------------------------------------
# 核だけの venv で README の例を pyright と mypy --strict にかける段の、判じ方。本物の pyright・mypy の出力の写しで確かめる。


def _doeff_readme_installed() -> object:
    """根の README(doeff の配布物の README)と doeff-vm の README を持つ、入れた物の写し。"""
    readmes = tuple(
        (ROOT / ("README.md" if package == "doeff" else f"packages/{package}/README.md")).read_text(
            encoding="utf-8"
        )
        for package in tool.CHECKED_READMES
    )
    return tool.Installed(Path("venv/bin/python"), "3.12.14", False, (), readmes)


def test_the_type_checked_examples_are_the_python_ones_the_core_only_venv_runs() -> None:
    examples = tool.type_checked_examples(_doeff_readme_installed(), "doeff")
    assert examples
    assert {example.language for example in examples} == {"python"}
    # README の Quick Start が package の頭から名を取る例が、型の check の対象に入っている(card の抜け 1 の形)。
    assert any(re.search(r"from doeff_core_effects import ", example.code) for example in examples)
    # Hy の module を読む例(extra hy)は入らない。
    assert not any(
        re.search(r"^\s*(import hy\b|from hy\b)", e.code, re.MULTILINE) for e in examples
    )


def _pyright_report(*diagnostics: tuple[str, str], analyzed: int = 4) -> dict[str, object]:
    """pyright --outputjson の写し(規則と文の組から)。"""
    return {
        "version": "1.1.414",
        "generalDiagnostics": [
            {
                "file": "/work/typed/doeff/example_21.py",
                "severity": "error",
                "message": message,
                "range": {
                    "start": {"line": 2, "character": 31},
                    "end": {"line": 2, "character": 34},
                },
                "rule": rule,
            }
            for rule, message in diagnostics
        ],
        "summary": {"filesAnalyzed": analyzed, "errorCount": len(diagnostics)},
    }


NOT_EXPORTED = (
    "reportPrivateImportUsage",
    '"Ask" is not exported from module "doeff_core_effects"\n  Import from "doeff_core_effects.effects" instead',
)


def test_pyright_naming_a_private_import_is_a_failure() -> None:
    failures = tool.pyright_failures(_pyright_report(NOT_EXPORTED), checked=4)
    assert len(failures) == 1
    assert "example_21.py:3" in failures[0]
    assert '"Ask" is not exported from module "doeff_core_effects"' in failures[0]


def test_pyright_complaints_about_the_example_itself_are_not_this_checks_business() -> None:
    other = (
        "reportArgumentType",
        'Argument of type "int" cannot be assigned to parameter "x" of type "str"',
    )
    assert tool.pyright_failures(_pyright_report(other), checked=4) == []


def test_pyright_not_finding_the_package_is_a_failure() -> None:
    # venv の python を見つけられないと、import が全部「見つからない」になる。何も言わない緑ではなく、赤にする。
    missing = ("reportMissingImports", 'Import "doeff_core_effects" could not be resolved')
    assert len(tool.pyright_failures(_pyright_report(missing), checked=4)) == 1


def test_pyright_that_read_fewer_files_than_were_written_is_a_failure() -> None:
    failures = tool.pyright_failures(_pyright_report(analyzed=1), checked=4)
    assert len(failures) == 1
    assert "1" in failures[0]
    assert "4" in failures[0]


MYPY_EXAMPLE = "/work/typed/doeff/example_21.py"
MYPY_NOT_EXPORTED = (
    f'{MYPY_EXAMPLE}:3: error: Module "doeff_core_effects" does not explicitly export attribute "Tell"'
    "  [attr-defined]"
)
MYPY_UNTYPED = (
    f"{MYPY_EXAMPLE}:8: error: Function is missing a return type annotation  [no-untyped-def]"
)


def test_mypy_naming_an_unexported_attribute_is_a_failure() -> None:
    output = (
        f"{MYPY_NOT_EXPORTED}\n{MYPY_UNTYPED}\nFound 2 errors in 1 file (checked 4 source files)\n"
    )
    failures = tool.mypy_failures(output, checked=4)
    assert failures == [MYPY_NOT_EXPORTED]


def test_mypy_strict_complaints_about_the_example_itself_are_not_this_checks_business() -> None:
    output = f"{MYPY_UNTYPED}\nFound 1 error in 1 file (checked 4 source files)\n"
    assert tool.mypy_failures(output, checked=4) == []
    assert tool.mypy_failures("Success: no issues found in 4 source files\n", checked=4) == []


def test_mypy_not_finding_the_package_is_a_failure() -> None:
    output = (
        f'{MYPY_EXAMPLE}:3: error: Cannot find implementation or library stub for module named "doeff_core_effects"'
        "  [import-not-found]\nFound 1 error in 1 file (checked 4 source files)\n"
    )
    assert len(tool.mypy_failures(output, checked=4)) == 1


@pytest.mark.parametrize(
    "output",
    [
        "Success: no issues found in 1 source file\n",
        "Found 3 errors in 1 file (checked 1 source file)\n",
        "",
    ],
    ids=["success-line", "found-line", "no-summary"],
)
def test_mypy_that_read_fewer_files_than_were_written_is_a_failure(output: str) -> None:
    assert len(tool.mypy_failures(output, checked=4)) == 1


def test_ci_runs_the_tool_on_every_python_of_the_compatibility_matrix() -> None:
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    job = jobs["core-only-install"]
    assert job["strategy"]["matrix"] == jobs["pytest-matrix"]["strategy"]["matrix"]
    runs = [step.get("run", "") for step in job["steps"]]
    assert any(
        "tools/verify_core_install.py --python ${{ matrix.python-version }}" in run for run in runs
    ), runs
