# moved-submodule-shim: doeff_claude_code.lines -> doeff_claude_code._lines
# moved-submodule-shim-names doeff_claude_code.lines:
#   ACCOUNT_REFUSAL_ERRORS ASSISTANT_ERROR_RATE_LIMIT AccountLimitHit AccountRefusalHit
#   AgentToolResult AssistantMessage BackendLost COMPACTING_STATUS COMPACT_ABORTED_ERROR
#   CONTINUING_STOP_REASONS CallClosed CallOpened CallStep ClaudeLineKind ClaudeStreamLine
#   ClaudeTurn ClaudeTurnEnd CompactBoundary CompactEnded CompactOutcome CompactStarted
#   CompactTrigger Completed ControlResponse CostMark DeltaKind FRESH_MARK Failed HookNotice
#   HookPhase INPUT_FATES INPUT_FATE_TERMINAL Init InputFate Interrupted MODEL_COST_TOLERANCE_USD
#   MODEL_USAGE_COUNTS ModelRefusalHit ModelUsage ModelUsageGap ModelUsageTally ModelWindow Other
#   PartialMessage PermissionRequested RATE_LIMIT_REJECTED RateLimit RequestTiming STDERR_TAIL_CHARS
#   STOP_HOOK_FEEDBACK_HEAD STOP_REASON_REFUSAL SUBAGENT_ANSWERED_STATUS SUBAGENT_TASK_TYPE
#   SYNTHETIC_MODEL StopHookFeedback SubagentCall SubagentCalls SubagentTranscript
#   SubagentTranscriptProblem TaskEvent ThinkingTokens TimedPhase ToolAnswer ToolCall ToolResult
#   TurnResult Usage agent_tool_result_of call_end_reason_of call_end_usage_of call_opened_of
#   call_step classify_assistant classify_control_request classify_control_response
#   classify_lifecycle classify_rate_limit classify_record classify_result classify_stream_event
#   classify_system classify_user closed_usage compact_boundary_of compact_ended_of content_blocks
#   delta_kind_of hook_notice_of instant_at int_at last_call_closed mark_of_result merged_windows
#   model_cost_gap model_usage_increment model_usage_of model_windows_of number_at object_at
#   own_records parse_record per_turn_model_usage positive_or recorded_cost reported_final_call
#   request_timing_of stated_or stderr_tail_within strings_at subagent_calls_of
#   subagent_transcript_of text_at thinking_start_of timed_phases_of tool_answer_of tool_start_of
#   transcript_record transcript_records usage_at_mark usage_of whole_ms_of
"""Moved: ``doeff_claude_code.lines`` is now ``doeff_claude_code._lines`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_claude_code.lines…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_claude_code._lines import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_claude_code._lines", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_claude_code._lines")
