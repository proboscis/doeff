"""核だけの入れ方の検の道具 tools/verify_core_install.py が、壊れた入れ方を名指す事の失敗ケース。

道具は Python の版ごとに、本物の install(核の 4 個だけの新しい venv)で確かめる。道具の判じ方が黙って緑を返すと、核だけの
入れ方が壊れても道具は落ちない。ここでは判じ方を、入れた物と子の process の結末の写しで確かめる(本物の install は doeff-vm
を組むので 1 版 約 1 分かかり、開発の環境の検には入れない)。
"""

from __future__ import annotations

import functools
import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import get_args

import pytest
import tomllib
import yaml
from doeff_core_effects import _extra_hints as extra_hints

from doeff import handler, run
from doeff.cli.demo import budget, canned, digest, trace

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "python-compatibility.yml"
PYPI_WORKFLOW = ROOT / ".github" / "workflows" / "core-install-from-pypi.yml"


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
    return tool.Distribution(name, "0.6.0", (ROOT / directory).resolve().as_uri(), editable)


def _from_index(name: str, version: str = "0.6.0") -> object:
    """index(PyPI)から入った配布物の写し。"""
    return tool.Distribution(name, version, "", False)


def _installed(*distributions: object) -> object:
    """入った物の判じ方だけを試すための、venv の様子の写し(README は読まない)。"""
    return tool.Installed(Path("venv/bin/python"), "3.12.14", False, distributions, ("", ""))


CORE_FROM_WHEELS = (
    _from_checkout("doeff"),
    _from_checkout("doeff-vm"),
    _from_checkout("doeff-core-effects"),
    _from_index("frozendict", "2.4.7"),
)
CORE_FROM_PYPI = (
    _from_index("doeff"),
    _from_index("doeff-vm"),
    _from_index("doeff-core-effects"),
    _from_index("frozendict", "2.4.7"),
)


def test_the_core_four_built_from_the_checkout_pass() -> None:
    assert tool.install_problems(_installed(*CORE_FROM_WHEELS), tool.Checkout()) == []


def test_an_install_that_brings_more_than_the_core_four_is_named() -> None:
    # 0.5 までの既定(doeff-hy と httpx を既定の依存に持つ)の入れ方の一部。
    problems = tool.install_problems(
        _installed(
            *CORE_FROM_WHEELS,
            _from_index("doeff-hy"),
            _from_index("httpx", "0.28.1"),
        ),
        tool.Checkout(),
    )
    assert len(problems) == 1
    assert "余計: ['doeff-hy', 'httpx']・足りない: []" in problems[0]


def test_an_editable_or_index_install_of_a_checkout_package_is_named() -> None:
    # editable は checkout を直に読み(wheel の中身を確かめない)、index の物は checkout の物でない。
    problems = tool.install_problems(
        _installed(
            _from_checkout("doeff", editable=True),
            _from_index("doeff-vm"),
            _from_checkout("doeff-core-effects"),
            _from_index("frozendict", "2.4.7"),
        ),
        tool.Checkout(),
    )
    assert [problem.split(" ")[0] for problem in problems] == ["doeff", "doeff-vm"]
    assert "(editable)" in problems[0]
    assert "入れた所: index" in problems[1]


@pytest.mark.parametrize("version", [None, "0.6.0"], ids=["latest", "named"])
def test_the_core_four_from_pypi_pass(version: str | None) -> None:
    assert tool.install_problems(_installed(*CORE_FROM_PYPI), tool.PyPI(version)) == []


def test_a_checkout_or_editable_package_is_not_the_pypi_one() -> None:
    # PyPI の確かめに、手元の checkout から入った物が混ざると、利用者の入れる物を確かめた事にならない。
    problems = tool.install_problems(
        _installed(
            _from_checkout("doeff"),
            _from_index("doeff-vm"),
            _from_checkout("doeff-core-effects", editable=True),
            _from_index("frozendict", "2.4.7"),
        ),
        tool.PyPI(None),
    )
    assert [problem.split(" ")[0] for problem in problems] == ["doeff", "doeff-core-effects"]
    assert all("PyPI から入っていない" in problem for problem in problems)
    assert "(editable)" in problems[1]


def test_a_pypi_package_of_another_version_than_the_named_one_is_named() -> None:
    problems = tool.install_problems(
        _installed(
            _from_index("doeff"),
            _from_index("doeff-vm", "0.5.0"),
            _from_index("doeff-core-effects"),
            _from_index("frozendict", "2.4.7"),
        ),
        tool.PyPI("0.6.0"),
    )
    assert problems == ["doeff-vm が名指した版 0.6.0 でない — 入った版: 0.5.0"]


def test_pypi_installs_take_only_a_wheel_of_the_rust_vm() -> None:
    # GitHub の runner には Rust の道具が在るので、wheel が無い機体でも source から組んで通ってしまう。
    arguments = tool.install_arguments(tool.PyPI(None))
    assert arguments[arguments.index("--only-binary") + 1] == "doeff-vm"
    assert arguments[-1] == "doeff"
    assert tool.install_arguments(tool.PyPI("0.6.0"))[-1] == "doeff==0.6.0"


@pytest.mark.parametrize(
    ("value", "source"),
    [(None, tool.Checkout()), ("latest", tool.PyPI(None)), ("0.6.0", tool.PyPI("0.6.0"))],
)
def test_the_pypi_option_names_the_source(value: str | None, source: object) -> None:
    assert tool.parse_source(value) == source


HY_LINE = (
    "doeff_core_effects._extra_hints.MissingExtraError: No module named 'doeff_hy' — Hy modules "
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


# --- 試しの命令 python -m doeff demo が核だけの venv で動く --------------------------
DEMO_LIVE = repr(f"{len(tool.DEMO_PAGE)} chars")
DEMO_OUTPUT = (
    f">>> run(handler(canned)(digest(url)))\n{tool.DEMO_CANNED_ANSWER}\n\n"
    ">>> run(handler(canned)(handler(trace)(digest(url))))\n"
    "trace: Fetch(url='file:///page.html')\n"
    f"{tool.DEMO_TRACE_LINE}\n{tool.DEMO_CANNED_ANSWER}\n\n"
    f">>> run(handler(canned)(budget(1)(digest(url))))\n{tool.DEMO_BUDGET_ANSWER}\n\n"
    f">>> run(handler(canned)(handler(live_world)(digest(url))))\n{DEMO_LIVE}\n\n"
)


@pytest.mark.parametrize(
    ("returncode", "stdout", "passes"),
    [
        (0, DEMO_OUTPUT, True),
        (1, DEMO_OUTPUT, False),
        (0, DEMO_OUTPUT.replace(f"{tool.DEMO_TRACE_LINE}\n", ""), False),
        (0, DEMO_OUTPUT.replace(tool.DEMO_BUDGET_ANSWER, "'14 chars'"), False),
        (0, DEMO_OUTPUT.replace(DEMO_LIVE, ""), False),
        (
            0,
            f"{DEMO_LIVE}\n{tool.DEMO_BUDGET_ANSWER}\n{tool.DEMO_TRACE_LINE}\n{tool.DEMO_CANNED_ANSWER}\n",
            False,
        ),
        (0, DEMO_OUTPUT.replace(f"\n{DEMO_LIVE}\n", f"\nx {DEMO_LIVE}\n"), False),
    ],
    ids=[
        "four-handlers-in-order",
        "exit-1",
        "no-trace",
        "budget-did-not-stop",
        "no-live",
        "reversed",
        "not-own-line",
    ],
)
def test_the_demo_passes_only_when_it_prints_the_four_answers_in_order(
    returncode: int, stdout: str, passes: bool
) -> None:
    result = subprocess.CompletedProcess(["python"], returncode, stdout, "")
    assert (tool.demo_failure(result) is None) is passes


def test_the_demo_check_expects_the_answers_of_the_demos_handlers(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # 道具は標準ライブラリだけで走るので doeff を import できず、答えを写して持つ — 写しが demo の答えとずれない事。
    assert repr(run(handler(canned)(digest(tool.DEMO_PAGE)))) == tool.DEMO_CANNED_ANSWER
    assert repr(run(handler(canned)(budget(1)(digest(tool.DEMO_PAGE))))) == tool.DEMO_BUDGET_ANSWER
    run(handler(canned)(handler(trace)(digest(tool.DEMO_PAGE))))
    assert tool.DEMO_TRACE_LINE in capsys.readouterr().out.splitlines()


def test_the_demo_check_runs_the_command_the_readme_points_to() -> None:
    command = " ".join(("python", *tool.DEMO_ARGUMENTS[1:]))
    assert command == "python -m doeff demo"
    assert f"`{command}`" in (ROOT / "README.md").read_text(encoding="utf-8")


# --- LLM の agent の例と、その pytest の検の例が核の 4 個(と pytest)だけで通る -------------
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


# --- 紹介の記事の例が核の 4 個(と pytest)だけで、記事の見せる行を印字して通る ---------------------------
PRODUCTION = tool.ArticleScript("production.py", ("SMTP -> x", "Welcome, Grace!"))
FANOUT = tool.ArticleScript("fanout.py", ("['a']", "['A']"), timed=True)


@pytest.mark.parametrize(
    ("script", "returncode", "stdout", "stderr", "passes"),
    [
        (PRODUCTION, 0, "SMTP -> x\nWelcome, Grace!\n", "", True),
        (PRODUCTION, 1, "SMTP -> x\nWelcome, Grace!\n", "", False),
        (
            PRODUCTION,
            0,
            "SMTP -> x\nWelcome, Grace!\n",
            "ResourceWarning: unclosed database\n",
            False,
        ),
        (PRODUCTION, 0, "Welcome, Grace!\nSMTP -> x\n", "", False),
        (PRODUCTION, 0, "SMTP -> x\nWelcome, Grace!\nmore\n", "", False),
        (FANOUT, 0, "['a']\n['A']\n0.21 s\n", "", True),
        (FANOUT, 0, "['a']\n['A']\n0.61 s\n", "", False),
        (FANOUT, 0, "['a']\n['A']\n", "", False),
        (FANOUT, 0, "['a']\n['A']\nabout 0.2 s\n", "", False),
        (FANOUT, 0, "", "", False),
    ],
    ids=[
        "lines",
        "exit-1",
        "warning-on-stderr",
        "other-order",
        "extra-line",
        "concurrent",
        "one-by-one",
        "no-seconds",
        "seconds-not-a-number",
        "no-output",
    ],
)
def test_an_article_script_passes_only_when_it_prints_the_articles_lines(
    script: object, returncode: int, stdout: str, stderr: str, passes: bool
) -> None:
    result = subprocess.CompletedProcess(["python"], returncode, stdout, stderr)
    assert (tool.article_script_failure(script, result) is None) is passes


@pytest.mark.parametrize(
    ("returncode", "stdout", "passes"),
    [
        (0, ".......\n7 passed in 0.27s\n", True),
        (0, ".......\n7 passed, 1 warning in 0.27s\n", False),
        (0, "......s\n6 passed, 1 skipped in 0.27s\n", False),
        (1, "...F...\n1 failed, 6 passed in 0.27s\n", False),
        (5, "\nno tests ran in 0.01s\n", False),
        (0, "", False),
    ],
    ids=["passed", "warning", "skipped", "failed", "no-tests", "no-output"],
)
def test_the_article_tests_pass_only_when_every_test_passes_cleanly(
    returncode: int, stdout: str, passes: bool
) -> None:
    result = subprocess.CompletedProcess(["python"], returncode, stdout, "")
    assert (tool.article_tests_failure(result) is None) is passes


# 記事の例の dir の、他の file が import するだけで何も印字しない module。
ARTICLE_MODULES = ("infra.py", "plain_app.py", "welcome.py")


def test_the_article_check_runs_every_script_of_the_article() -> None:
    # 記事の例の印字を比べるのは道具の ARTICLE_SCRIPTS だけ— 表が dir の script の全部を名指し、記事の
    # 書く切り替え DOEFF_SCHEDULER=rust でも fanout.py を走らせる事。例の dir に script を足して表に書き忘れると赤になる。
    directory = ROOT / tool.ARTICLE_EXAMPLE_DIRECTORY
    scripts = sorted(
        path.name
        for path in directory.glob("*.py")
        if not path.name.startswith("test_") and path.name not in ARTICLE_MODULES
    )
    assert sorted({script.name for script in tool.ARTICLE_SCRIPTS}) == scripts
    assert all((directory / name).is_file() for name in ARTICLE_MODULES)
    assert {script.name for script in tool.ARTICLE_SCRIPTS if script.timed} == {"fanout.py"}
    assert [script.label() for script in tool.ARTICLE_SCRIPTS if script.scheduler] == [
        "DOEFF_SCHEDULER=rust fanout.py"
    ]
    assert (directory / "pytest.ini").is_file()


@pytest.mark.parametrize("page", ["README.md", "docs/index.md"])
def test_the_pages_that_say_the_llm_example_needs_only_doeff_link_to_it(page: str) -> None:
    # 道具が確かめる約束(pip install doeff だけで走る)を言う page が、確かめる例そのものを指している事。
    assert "examples/llm_agent.py" in (ROOT / page).read_text(encoding="utf-8")


# 根の README の「Hy の module を Python から読む」節の形(足した時に、核の 4 個だけの venv で落ちた)。
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


# --- GitHub の README を入った版で走らせる ----------------------------------------------------------
# --pypi は、入った wheel の README(入った版と同じ時に書いた物)の他に、checkout(CI では GitHub の main)の README を入った版の
# venv で走らせる。wheel の README だけでは、GitHub の README が出した版より先へ進んでも緑のまま食い違いを見ない。

# 入った版と同じ時に書いた README — 例は入った版で通る。
_OLD_README = """# doeff

## Quick Start

```python
print(1)  # 1
```
"""
# 出した版より先へ進んだ README — 2 つ目の例は、入った版に無い使い方で落ちる。
_NEW_README = """# doeff

## Quick Start

```python
print(1)  # 1
```

## Writing a handler

```python
print(2)  # 2
raise RuntimeError("handler must return DoExpr, got: <class 'generator'>")
```
"""


def _checkout_with_readmes(repo: Path, text: str) -> None:
    """CHECKED_READMES の配布物の README を、checkout の写しの repo に置く。"""
    for readme in tool.checkout_readmes(ROOT).readmes:
        (repo / readme.source).parent.mkdir(parents=True, exist_ok=True)
        (repo / readme.source).write_text(text, encoding="utf-8")


def _venv_with_readmes(text: str) -> object:
    """README の例を走らせる interpreter(検の interpreter — 例は doeff を使わない)と、wheel の README の写し。"""
    readmes = tuple(text for _ in tool.CHECKED_READMES)
    return tool.Installed(Path(sys.executable), "3.12.14", False, (), readmes)


def test_the_checkout_readmes_are_the_files_the_packages_put_in_their_wheels() -> None:
    readmes = tool.checkout_readmes(ROOT)
    assert [readme.package for readme in readmes.readmes] == list(tool.CHECKED_READMES)
    directories = {package.name: package.directory for package in tool.FROM_CHECKOUT}
    for readme in readmes.readmes:
        project = tomllib.loads(
            (ROOT / directories[readme.package] / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]
        # wheel の metadata の本文に入る file と、GitHub の README として読む file が同じ。
        assert project["readme"] == tool.README_FILE
        assert readme.source == (directories[readme.package] / tool.README_FILE).as_posix()
        assert readme.text == (ROOT / readme.source).read_text(encoding="utf-8")


def test_the_wheel_and_the_github_readmes_are_counted_apart(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _checkout_with_readmes(repo, _NEW_README)
    installed = _venv_with_readmes(_OLD_README)
    wheel = tool.check_readmes(tool.wheel_readmes(installed), installed, tmp_path / "wheel", 60)
    github = tool.check_readmes(tool.checkout_readmes(repo), installed, tmp_path / "github", 60)
    # wheel の README は入った版に合うので通り、GitHub の README の先へ進んだ例だけが落ちる。
    assert (wheel.title, wheel.ran, wheel.failed, wheel.failures) == (
        tool.WHEEL_READMES,
        2,
        (),
        (),
    )
    assert (github.title, github.ran) == (tool.GITHUB_READMES, 4)
    assert github.failed == (
        tool.FailedExample(
            "README.md:11",
            "Writing a handler",
            "RuntimeError: handler must return DoExpr, got: <class 'generator'>",
        ),
        tool.FailedExample(
            "packages/doeff-vm/README.md:11",
            "Writing a handler",
            "RuntimeError: handler must return DoExpr, got: <class 'generator'>",
        ),
    )
    assert len(github.failures) == 2


def test_the_count_says_how_many_github_examples_pass_on_the_installed_version() -> None:
    tally = tool.ReadmeTally(
        tool.GITHUB_READMES,
        ran=7,
        failed=(
            tool.FailedExample("README.md:40", "Quick Start", "RuntimeError: handler must return"),
            tool.FailedExample("README.md:80", "Handlers", "ImportError: cannot import name 'x'"),
        ),
        failures=("…", "…"),
    )
    assert tally.summary() == [
        "GitHub の README の例 7 個のうち入った版で通るのは 5 個",
        "  NG  節「Quick Start」(README.md:40): RuntimeError: handler must return",
        "  NG  節「Handlers」(README.md:80): ImportError: cannot import name 'x'",
    ]


@pytest.mark.parametrize(
    ("failure", "line"),
    [
        (
            "README.md:13(Writing a handler の python)が終了コード 1 で落ちた — 例を今の使い方に直す\n2\n"
            'Traceback (most recent call last):\n  File "x.py", line 2\nRuntimeError: boom\n',
            "RuntimeError: boom",
        ),
        ("README.md:5(Quick Start の python)の印字が print の行の注記と違う", None),
        ("README.md:5(Quick Start の python)が 60 秒で終わらなかった", None),
    ],
    ids=["exception", "stated-output", "timeout"],
)
def test_the_error_line_is_the_exception_or_the_one_line_reason(
    failure: str, line: str | None
) -> None:
    assert tool.error_line(failure) == (failure if line is None else line)


_STAGES = (
    "check_public_names",
    "check_extra_uses",
    "check_demo",
    "check_llm_example",
    "check_article_examples",
)


def _check_python_titles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: object
) -> list[str]:
    """check_python が例を走らせた README の組の名を、install と他の段を写しに替えて読む。"""
    installed = _venv_with_readmes(_OLD_README)
    titles: list[str] = []

    def check_readmes(readmes: object, *_: object) -> object:
        titles.append(readmes.title)
        return tool.ReadmeTally(readmes.title, 0, (), ())

    monkeypatch.setattr(tool, "install", lambda *_: installed)
    monkeypatch.setattr(tool, "install_problems", lambda *_: [])
    monkeypatch.setattr(tool, "check_readmes", check_readmes)
    for stage in _STAGES:
        monkeypatch.setattr(tool, stage, lambda *_: [])
    assert tool.check_python("3.12", tmp_path, 60, source) == []
    return titles


def _stages_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: object) -> list[str]:
    """check_python が呼んだ段の名を、その順に読む — 各段の写しは自分の名を落ちた訳として返す(訳の列は呼んだ順に並ぶ)。"""
    installed = _venv_with_readmes(_OLD_README)
    monkeypatch.setattr(tool, "install", lambda *_: installed)
    monkeypatch.setattr(tool, "install_problems", lambda *_: [])
    monkeypatch.setattr(
        tool, "check_readmes", lambda readmes, *_: tool.ReadmeTally(readmes.title, 0, (), ())
    )
    for stage in _STAGES:
        monkeypatch.setattr(tool, stage, functools.partial(lambda name, *_: [name], stage))
    return tool.check_python("3.12", tmp_path, 60, source)


def test_only_the_pypi_check_runs_the_article_examples(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # 記事の例は PyPI の版に留めて書く(ADR-DOE-API-002 R3)ので、checkout の版では走らせない。
    # --pypi は venv に pytest を足すので最後に走らせる。
    checkout_stages = _stages_run(monkeypatch, tmp_path / "checkout", tool.Checkout())
    pypi_stages = _stages_run(monkeypatch, tmp_path / "pypi", tool.PyPI(None))
    assert "check_article_examples" not in checkout_stages
    assert pypi_stages[-1] == "check_article_examples"


def test_the_pypi_check_runs_the_github_readmes_besides_the_wheel_ones(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    titles = _check_python_titles(monkeypatch, tmp_path, tool.PyPI(None))
    assert titles == [tool.WHEEL_READMES, tool.GITHUB_READMES]


def test_the_checkout_check_runs_the_readmes_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # checkout から組んだ wheel の README は checkout の README と同じ物。
    assert _check_python_titles(monkeypatch, tmp_path, tool.Checkout()) == [tool.WHEEL_READMES]


def test_a_push_that_changes_a_github_readme_runs_the_daily_pypi_check() -> None:
    paths = yaml.safe_load(PYPI_WORKFLOW.read_text(encoding="utf-8"))[True]["push"]["paths"]
    assert {readme.source for readme in tool.checkout_readmes(ROOT).readmes} <= set(paths)


# --- 型の check が「公開していない名」を名指す ---------------------------------------------------
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
    '"Ask" is not exported from module "doeff_core_effects"\n  Import from "doeff_core_effects._effects" instead',
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


def _promised_pythons() -> list[str]:
    """doeff-vm の classifiers が約束する Python の版(PyPI の頁が見せる物 — その版ごとに wheel が要る)。"""
    project = tomllib.loads(
        (ROOT / "packages" / "doeff-vm" / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    prefix = "Programming Language :: Python :: 3."
    return [
        classifier.removeprefix("Programming Language :: Python :: ")
        for classifier in project["classifiers"]
        if classifier.startswith(prefix) and classifier.removeprefix(prefix).isdigit()
    ]


def test_the_daily_pypi_check_installs_every_promised_python_on_three_systems() -> None:
    workflow = yaml.safe_load(PYPI_WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML は鍵の on を真偽の True と読む(YAML 1.1)。
    triggers = workflow[True]
    assert {"schedule", "workflow_dispatch"} <= triggers.keys()
    (job,) = workflow["jobs"].values()
    matrix = job["strategy"]["matrix"]
    assert matrix["os"] == ["ubuntu-latest", "macos-latest", "windows-latest"]
    assert matrix["python"] == _promised_pythons()
    uses = [step.get("uses", "") for step in job["steps"]]
    # Rust の道具が在ると、wheel の無い機体でも doeff-vm を source から組んで通ってしまう。
    assert not any("rust" in use for use in uses), uses
    runs = [step.get("run", "") for step in job["steps"]]
    assert any(
        "tools/verify_core_install.py --pypi latest --python ${{ matrix.python }}" in run
        for run in runs
    ), runs
