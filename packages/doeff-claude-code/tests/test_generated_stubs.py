"""doeff-claude-code の Hy の module の型の宣言のうち、doeff_hy.static_stub が作った .pyi の失敗ケース(#2843)。

使い手の repo が import する values・lines・faults・fake・effects は .pyi が無く、使い手がその名を使う行を足すと、strict の型の門が
書き手に直せない Unknown の赤で止まった。.pyi は道具が .hy から作る。

- 使い手が import する名を 1 つずつ束ねた検の module に、「型が分からない」の赤が出ない(.pyi を外すと赤になる)。
- 一致の検 = 作り直した物 == commit された物(.hy を変えたら `python -m doeff_hy.static_stub --write <.hy>` で作り直す)。
"""

import shutil
from pathlib import Path

import pytest

from doeff_hy.static_stub import UsedModule, stale_in, unknown_in_users

SOURCE = Path(__file__).resolve().parents[1] / "src"

# 使い手の repo の main が import する名(module ごと・2026-10-09 の数え — 使い手が型の宣言の写しを消すと、これらの名をこの package の
# .pyi から読む・#4257)。
USED = (
    UsedModule("values", ("FreshSession", "ResumeSession", "ForkSession", "ClaudeHome", "ClaudeSessionSpec", "TurnInput")),
    UsedModule(
        "effects",
        (
            "ClaudeStartTurn",
            "ClaudeInjectInput",
            "ClaudeInterruptTurn",
            "ClaudeReadTurnEvents",
            "ClaudeAnswerPermission",
            "ClaudeCloseSession",
            "ClaudeSessionStatus",
            "ClaudeExportSession",
            "TurnStarted",
            "ClaudeWarmSession",
            "SessionWarmed",
            "ClaudeLiveLimitExceeded",
            "SessionExported",
            "SessionNotFound",
        ),
    ),
    UsedModule("fake", ("FakeReply", "FakeClaudeWorld", "StopHookRejection")),
    UsedModule(
        "faults",
        ("ClaudeDropProcess", "ClaudeForgetSession", "ClaudeLiveProcess", "LiveProcess", "NoLiveProcess", "StopReason"),
    ),
    UsedModule(
        "lines",
        ("Usage", "ModelWindow", "AccountRefusalHit", "AccountLimitHit", "CompactBoundary", "CompactTrigger"),
    ),
)


@pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")
def test_names_the_users_import_are_not_unknown(tmp_path: Path) -> None:
    assert unknown_in_users(tmp_path, "doeff_claude_code", USED) == ()


def test_generated_stubs_are_what_the_tool_makes() -> None:
    assert [f"{s.source.relative_to(SOURCE)}: {s.reason}" for s in stale_in(SOURCE)] == []
