"""書き換えの道具(tools/former_factory_call_rewrite.py)が、素の handler になった名の旧い呼びを名そのものへ移す事。

1. Python の ``sequential()``・Hy の ``(sequential)`` を ``sequential`` へ書き換え、新しい呼び・値・定義し直す file は書き換えない。
2. 書き換えた text をもう一度書き換えても変わらない。

former-factory-call-rewrite: keep — 入力の文字列は旧い形をわざと書く。
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load() -> types.ModuleType:
    """tools/ の道具を読む(tools/ は package ではない)。"""
    loaded = sys.modules.get("former_factory_call_rewrite")
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(
        "former_factory_call_rewrite", ROOT / "tools" / "former_factory_call_rewrite.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["former_factory_call_rewrite"] = module
    spec.loader.exec_module(module)
    return module


tool = _load()
SEQUENTIAL = tool.targets(["sequential"])[0]
AWAIT = tool.targets(["await_handler"])[0]

PY = "from doeff_traverse import sequential\n"
HY = "(import doeff-traverse [sequential])\n"

REWRITTEN = [
    ("m.py", PY + "x = sequential()(main())\n", PY + "x = sequential(main())\n"),
    (
        "m.py",
        PY + "stack = [try_handler, sequential()]\n",
        PY + "stack = [try_handler, sequential]\n",
    ),
    (
        "m.py",
        "from doeff_traverse.handlers import sequential as seq\nx = seq()(main())  # 走らせる\n",
        "from doeff_traverse.handlers import sequential as seq\nx = seq(main())  # 走らせる\n",
    ),
    (
        "m.py",
        "import doeff_traverse as dt\nx = dt.sequential()(main())\n",
        "import doeff_traverse as dt\nx = dt.sequential(main())\n",
    ),
    ("m.hy", HY + "(run ((sequential) (main)))\n", HY + "(run (sequential (main)))\n"),
    (
        "m.hy",
        HY + "(with-stack [fail-handler (sequential)] p)\n",
        HY + "(with-stack [fail-handler sequential] p)\n",
    ),
    ("m.hy", HY + "(-> (main) (sequential) run)\n", HY + "(-> (main) sequential run)\n"),
    (
        "t.py",
        'CODE = """\n' + HY + '(run ((sequential) (main)))\n"""\n',
        'CODE = """\n' + HY + '(run (sequential (main)))\n"""\n',
    ),
    (
        "README.md",
        "doeff_traverse\n```python\nrun(sequential()(main()))\n```\n",
        "doeff_traverse\n```python\nrun(sequential(main()))\n```\n",
    ),
]


@pytest.mark.parametrize(("path", "before", "after"), REWRITTEN)
def test_the_former_call_becomes_the_name(path: str, before: str, after: str) -> None:
    rewritten, done = tool.rewrite_text(path, before, SEQUENTIAL)
    assert (rewritten, done) == (after, 1)
    assert tool.rewrite_text(path, rewritten, SEQUENTIAL) == (rewritten, 0)


def test_the_hy_name_with_a_hyphen_is_the_python_name() -> None:
    before = "(import doeff-core-effects [await-handler])\n(run ((await-handler) (main)))\n"
    rewritten, _ = tool.rewrite_text("m.hy", before, AWAIT)
    assert (
        rewritten == "(import doeff-core-effects [await-handler])\n(run (await-handler (main)))\n"
    )


LEFT = [
    ("m.py", PY + "x = sequential(main())\n", "新しい呼び"),
    ("m.py", PY + "stack = [sequential]\n", "値"),
    ("m.hy", HY + "(run (sequential (main)))\n", "新しい呼び"),
    ("m.py", "from other import sequential\nx = sequential()(main())\n", None),
]


@pytest.mark.parametrize(("path", "text", "form"), LEFT)
def test_what_is_not_the_former_call_is_left(path: str, text: str, form: str | None) -> None:
    assert tool.rewrite_text(path, text, SEQUENTIAL) == (text, 0)
    assert [site.form for _, site in tool.read_file(path, text, SEQUENTIAL)] == (
        [form] if form else []
    )


def test_a_file_defining_the_name_again_is_left_for_a_hand() -> None:
    text = PY + "x = sequential()(main())\ndef sequential():\n    pass\n"
    assert [site.form for _, site in tool.read_file("m.py", text, SEQUENTIAL)] == ["手で直す"]
    assert tool.rewrite_text("m.py", text, SEQUENTIAL) == (text, 0)
