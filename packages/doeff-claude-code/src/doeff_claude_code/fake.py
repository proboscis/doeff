# moved-submodule-shim: doeff_claude_code.fake -> doeff_claude_code._fake
# moved-submodule-shim-names doeff_claude_code.fake:
#   API_ERROR_SERVER ASSISTANT_ERROR_RATE_LIMIT AccountLimitHit AccountRefusalHit Answered
#   AssistantMessage AttachmentRefused BackendLost CLOCK_TICK CallClosed ClaudeAnswerPermission
#   ClaudeCloseSession ClaudeDropProcess ClaudeEmitOutsideTurn ClaudeExportSession
#   ClaudeForgetSession ClaudeInjectInput ClaudeInterruptTurn ClaudeLineKind ClaudeLiveLimitExceeded
#   ClaudeLiveProcess ClaudePrintOnce ClaudeReadTurnEvents ClaudeReplaceSessionCredential
#   ClaudeSessionSpec ClaudeSessionStatus ClaudeStartTurn ClaudeStreamLine ClaudeTurn ClaudeTurnEnd
#   ClaudeWarmSession Closed CompactBoundary CompactEnded CompactOutcome CompactStarted Completed
#   CredentialReplaced DELTA_SECONDS DeltaKind FAKE_CAPABILITIES FAKE_REQUEST_PREFIX FAKE_TASK_ID
#   FAKE_THINKING_PIECE FAKE_TOOL_INPUT_PIECE FAKE_TOOL_NAME FAKE_TOOL_USE_ID Failed FakeClaudeWorld
#   FakeInjection FakeReply FakeSession FakeTurn ForkSession FreshSession HomeCredentialSession
#   HookNotice HookPhase IMAGE_MIMES Idle Init InputAlreadyRead InputFate InputQueued
#   InterruptRequested Interrupted LentCredential LinkFromHome LiveProcess MIN_SLEEP
#   MODEL_REFUSAL_ERROR ModelRefusalHit ModelUsage ModelWindow NO_RECEIPT_CAPABILITIES NoLiveProcess
#   NoSuchRequest NoSuchSession NoTurnInFlight PartialMessage PermissionRequested PrintAnswered
#   PrintFailed PrintLimited PrintTimedOut QUICK_TURN_SECONDS RATE_LIMIT_REJECTED
#   REFUSAL_RESULT_SUBTYPE RateLimit Rebuilt ResumeSession STOP_REASON_REFUSAL SUBAGENT_TASK_TYPE
#   SYNTHETIC_MODEL SYNTHETIC_USAGE SessionClosed SessionExported SessionIdInUse SessionNotFound
#   SessionProcessDown SessionStatus SessionWarmed StopHookFeedback StopHookRejection StopReason
#   SubagentTranscript TaskEvent ToolAnswer ToolCall ToolResult TranscriptAbsent TranscriptPresent
#   TurnEventPage TurnInFlight TurnResult TurnRunning TurnStarted UnknownTurn Usage advance
#   api_error_turn begin_fake_turn begin_text call_tool carry_into complete_turn emit emit_all
#   emit_due_deltas emit_due_lines emit_limit end_scripted fake_answer fake_claude_code_handler
#   fake_claude_print_handler fake_close fake_drop fake_emit_outside fake_export fake_forget
#   fake_inject fake_interrupt fake_live_process fake_print fake_read_events fake_replace_credential
#   fake_start_turn fake_status fake_warm_refusal fake_warm_session finish
#   hyx_scripted_endXquestion_markX launch_key limit_hit_after line_due_at merged_windows
#   model_cost_gap model_refusal_after model_refuse_turn next_delta_at next_line_at note_over_limit
#   read_injections refusal_hit_after refuse_turn repeated_id reply_of ring_turn running_turn_of
#   said_by_reply stderr_tail_within stop_hook_notice_of task_lines_of tool_call_lines_of
#   transcript_of
"""Moved: ``doeff_claude_code.fake`` is now ``doeff_claude_code._fake`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_claude_code.fake…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_claude_code._fake import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_claude_code._fake", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_claude_code._fake")
