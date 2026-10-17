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
from doeff_core_effects import extra_hints

ROOT = Path(__file__).resolve().parents[1]


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
