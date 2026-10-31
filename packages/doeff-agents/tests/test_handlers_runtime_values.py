"""doeff_agents._handlers が型の検査の節で見せる外の package の名を、実行の時にも同じ物で配る事の検。

handlers は doeff-claude-code と doeff-codex の値(fake の世界・許可の方針・sandbox など — handler の factory の引数の
値)を、使い手がその package を import せずに読めるよう、``__getattr__`` で遅延して配る(``_RUNTIME_VALUES``)。
``if TYPE_CHECKING:`` の節が名を見せるのに表に足し忘れると、型の検査は通るのに実行の
``from doeff_agents._handlers import FakeCodexWorld`` が AttributeError になる(
FakeCodexWorld・CodexApprovalPolicy・CodexSandboxMode — 公開 API の目録は「__getattr__ が名を作れない」未分類と数えた)。
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import doeff_agents._handlers as handlers

SOURCE = Path(handlers.__file__)
OUTSIDE_ROOTS = ("doeff_claude_code", "doeff_codex")


def _type_checking_outside_imports() -> dict[str, tuple[str, str]]:
    """節が外の package から ``import 元の名 as 名`` で見せる名 → (定義元の module, 元の名)。"""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    return {
        alias.asname or alias.name: (stmt.module, alias.name)
        for node in tree.body
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
        for stmt in node.body
        if isinstance(stmt, ast.ImportFrom) and stmt.module and stmt.module.startswith(OUTSIDE_ROOTS)
        for alias in stmt.names
    }


def test_every_outside_name_the_type_checking_block_shows_is_served_at_run_time() -> None:
    shown = _type_checking_outside_imports()
    assert {"FakeCodexWorld", "CodexApprovalPolicy", "CodexSandboxMode", "FakeClaudeWorld"} <= set(shown)
    wrong = {
        name: (module, original)
        for name, (module, original) in shown.items()
        if getattr(handlers, name) is not getattr(importlib.import_module(module), original)
    }
    assert wrong == {}, f"節と実行で違う物を配る名: {wrong}"
