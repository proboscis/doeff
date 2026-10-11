"""doeff-codex の Hy の module の型の宣言のうち、doeff_hy.static_stub が作った .pyi の失敗ケース(agora-controllers の日次 10-11 12:00)。

使い手の repo(agora-controllers の agent_runtime.hy と codex の手番の検)が import する fake の名は .pyi が無く、strict の型の門が
書き手に直せない Unknown の赤で止まった(FakeCodexWorld・FakeReply の型が分からない・card acp:kanban-issue:ki-f63f65c153b0)。
.pyi は道具が .hy から作る(doeff-claude-code の tests/test_generated_stubs.py と同じ形)。

- 使い手が import する名を 1 つずつ束ねた検の module に、「型が分からない」の赤が出ない(.pyi を外すと赤になる)。
- 一致の検 = 作り直した物 == commit された物(.hy を変えたら `python -m doeff_hy.static_stub --write <.hy>` で作り直す)。
"""

import shutil
from pathlib import Path

import pytest

from doeff_hy.static_stub import UsedModule, stale_in, unknown_in_users

SOURCE = Path(__file__).resolve().parents[1] / "src"

# 使い手の repo の main が import する名(module ごと・2026-10-11 の数え)。
USED = (UsedModule("fake", ("FakeReply", "FakeCodexWorld")),)


@pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")
def test_names_the_users_import_are_not_unknown(tmp_path: Path) -> None:
    assert unknown_in_users(tmp_path, "doeff_codex", USED) == ()


def test_generated_stubs_are_what_the_tool_makes() -> None:
    assert [f"{s.source.relative_to(SOURCE)}: {s.reason}" for s in stale_in(SOURCE)] == []
