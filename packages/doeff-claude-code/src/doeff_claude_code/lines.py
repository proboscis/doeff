# moved-submodule-shim: doeff_claude_code.lines -> doeff_claude_code._lines
"""Moved: ``doeff_claude_code.lines`` is now ``doeff_claude_code._lines`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_claude_code.lines…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_claude_code._lines import ACCOUNT_REFUSAL_ERRORS as ACCOUNT_REFUSAL_ERRORS
    from doeff_claude_code._lines import ASSISTANT_ERROR_RATE_LIMIT as ASSISTANT_ERROR_RATE_LIMIT
    from doeff_claude_code._lines import COMPACT_ABORTED_ERROR as COMPACT_ABORTED_ERROR
    from doeff_claude_code._lines import COMPACTING_STATUS as COMPACTING_STATUS
    from doeff_claude_code._lines import CONTINUING_STOP_REASONS as CONTINUING_STOP_REASONS
    from doeff_claude_code._lines import FRESH_MARK as FRESH_MARK
    from doeff_claude_code._lines import INPUT_FATE_TERMINAL as INPUT_FATE_TERMINAL
    from doeff_claude_code._lines import INPUT_FATES as INPUT_FATES
    from doeff_claude_code._lines import MODEL_COST_TOLERANCE_USD as MODEL_COST_TOLERANCE_USD
    from doeff_claude_code._lines import MODEL_USAGE_COUNTS as MODEL_USAGE_COUNTS
    from doeff_claude_code._lines import RATE_LIMIT_REJECTED as RATE_LIMIT_REJECTED
    from doeff_claude_code._lines import STDERR_TAIL_CHARS as STDERR_TAIL_CHARS
    from doeff_claude_code._lines import STOP_HOOK_FEEDBACK_HEAD as STOP_HOOK_FEEDBACK_HEAD
    from doeff_claude_code._lines import STOP_REASON_REFUSAL as STOP_REASON_REFUSAL
    from doeff_claude_code._lines import SUBAGENT_ANSWERED_STATUS as SUBAGENT_ANSWERED_STATUS
    from doeff_claude_code._lines import SUBAGENT_TASK_TYPE as SUBAGENT_TASK_TYPE
    from doeff_claude_code._lines import SYNTHETIC_MODEL as SYNTHETIC_MODEL
    from doeff_claude_code._lines import AccountLimitHit as AccountLimitHit
    from doeff_claude_code._lines import AccountRefusalHit as AccountRefusalHit
    from doeff_claude_code._lines import AgentToolResult as AgentToolResult
    from doeff_claude_code._lines import AssistantMessage as AssistantMessage
    from doeff_claude_code._lines import BackendLost as BackendLost
    from doeff_claude_code._lines import Callable as Callable
    from doeff_claude_code._lines import CallClosed as CallClosed
    from doeff_claude_code._lines import CallOpened as CallOpened
    from doeff_claude_code._lines import CallStep as CallStep
    from doeff_claude_code._lines import ClaudeLineKind as ClaudeLineKind
    from doeff_claude_code._lines import ClaudeStreamLine as ClaudeStreamLine
    from doeff_claude_code._lines import ClaudeTurn as ClaudeTurn
    from doeff_claude_code._lines import ClaudeTurnEnd as ClaudeTurnEnd
    from doeff_claude_code._lines import CompactBoundary as CompactBoundary
    from doeff_claude_code._lines import CompactEnded as CompactEnded
    from doeff_claude_code._lines import CompactOutcome as CompactOutcome
    from doeff_claude_code._lines import CompactStarted as CompactStarted
    from doeff_claude_code._lines import CompactTrigger as CompactTrigger
    from doeff_claude_code._lines import Completed as Completed
    from doeff_claude_code._lines import ControlResponse as ControlResponse
    from doeff_claude_code._lines import CostMark as CostMark
    from doeff_claude_code._lines import DeltaKind as DeltaKind
    from doeff_claude_code._lines import Failed as Failed
    from doeff_claude_code._lines import FrozenJson as FrozenJson
    from doeff_claude_code._lines import FrozenMap as FrozenMap
    from doeff_claude_code._lines import HookNotice as HookNotice
    from doeff_claude_code._lines import HookPhase as HookPhase
    from doeff_claude_code._lines import Init as Init
    from doeff_claude_code._lines import InputFate as InputFate
    from doeff_claude_code._lines import Interrupted as Interrupted
    from doeff_claude_code._lines import ModelRefusalHit as ModelRefusalHit
    from doeff_claude_code._lines import ModelUsage as ModelUsage
    from doeff_claude_code._lines import ModelUsageGap as ModelUsageGap
    from doeff_claude_code._lines import ModelUsageTally as ModelUsageTally
    from doeff_claude_code._lines import ModelWindow as ModelWindow
    from doeff_claude_code._lines import Other as Other
    from doeff_claude_code._lines import PartialMessage as PartialMessage
    from doeff_claude_code._lines import PermissionRequested as PermissionRequested
    from doeff_claude_code._lines import RateLimit as RateLimit
    from doeff_claude_code._lines import RequestTiming as RequestTiming
    from doeff_claude_code._lines import StopHookFeedback as StopHookFeedback
    from doeff_claude_code._lines import StrEnum as StrEnum
    from doeff_claude_code._lines import SubagentCall as SubagentCall
    from doeff_claude_code._lines import SubagentCalls as SubagentCalls
    from doeff_claude_code._lines import SubagentTranscript as SubagentTranscript
    from doeff_claude_code._lines import SubagentTranscriptProblem as SubagentTranscriptProblem
    from doeff_claude_code._lines import TaskEvent as TaskEvent
    from doeff_claude_code._lines import ThinkingTokens as ThinkingTokens
    from doeff_claude_code._lines import TimedPhase as TimedPhase
    from doeff_claude_code._lines import ToolAnswer as ToolAnswer
    from doeff_claude_code._lines import ToolCall as ToolCall
    from doeff_claude_code._lines import ToolResult as ToolResult
    from doeff_claude_code._lines import TurnResult as TurnResult
    from doeff_claude_code._lines import Usage as Usage
    from doeff_claude_code._lines import agent_tool_result_of as agent_tool_result_of
    from doeff_claude_code._lines import call_end_reason_of as call_end_reason_of
    from doeff_claude_code._lines import call_end_usage_of as call_end_usage_of
    from doeff_claude_code._lines import call_opened_of as call_opened_of
    from doeff_claude_code._lines import call_step as call_step
    from doeff_claude_code._lines import classify_assistant as classify_assistant
    from doeff_claude_code._lines import classify_control_request as classify_control_request
    from doeff_claude_code._lines import classify_control_response as classify_control_response
    from doeff_claude_code._lines import classify_lifecycle as classify_lifecycle
    from doeff_claude_code._lines import classify_rate_limit as classify_rate_limit
    from doeff_claude_code._lines import classify_record as classify_record
    from doeff_claude_code._lines import classify_result as classify_result
    from doeff_claude_code._lines import classify_stream_event as classify_stream_event
    from doeff_claude_code._lines import classify_system as classify_system
    from doeff_claude_code._lines import classify_user as classify_user
    from doeff_claude_code._lines import closed_usage as closed_usage
    from doeff_claude_code._lines import compact_boundary_of as compact_boundary_of
    from doeff_claude_code._lines import compact_ended_of as compact_ended_of
    from doeff_claude_code._lines import content_blocks as content_blocks
    from doeff_claude_code._lines import dataclass as dataclass
    from doeff_claude_code._lines import datetime as datetime
    from doeff_claude_code._lines import delta_kind_of as delta_kind_of
    from doeff_claude_code._lines import field as field
    from doeff_claude_code._lines import fields as fields
    from doeff_claude_code._lines import freeze_json as freeze_json
    from doeff_claude_code._lines import frozen_json_object as frozen_json_object
    from doeff_claude_code._lines import hook_notice_of as hook_notice_of
    from doeff_claude_code._lines import instant_at as instant_at
    from doeff_claude_code._lines import int_at as int_at
    from doeff_claude_code._lines import jsonl_lines as jsonl_lines
    from doeff_claude_code._lines import last_call_closed as last_call_closed
    from doeff_claude_code._lines import mark_of_result as mark_of_result
    from doeff_claude_code._lines import merged_windows as merged_windows
    from doeff_claude_code._lines import model_cost_gap as model_cost_gap
    from doeff_claude_code._lines import model_usage_increment as model_usage_increment
    from doeff_claude_code._lines import model_usage_of as model_usage_of
    from doeff_claude_code._lines import model_windows_of as model_windows_of
    from doeff_claude_code._lines import number_at as number_at
    from doeff_claude_code._lines import object_at as object_at
    from doeff_claude_code._lines import own_records as own_records
    from doeff_claude_code._lines import parse_record as parse_record
    from doeff_claude_code._lines import per_turn_model_usage as per_turn_model_usage
    from doeff_claude_code._lines import positive_or as positive_or
    from doeff_claude_code._lines import recorded_cost as recorded_cost
    from doeff_claude_code._lines import replace as replace
    from doeff_claude_code._lines import reported_final_call as reported_final_call
    from doeff_claude_code._lines import request_timing_of as request_timing_of
    from doeff_claude_code._lines import run as run
    from doeff_claude_code._lines import stated_or as stated_or
    from doeff_claude_code._lines import stderr_tail_within as stderr_tail_within
    from doeff_claude_code._lines import strings_at as strings_at
    from doeff_claude_code._lines import subagent_calls_of as subagent_calls_of
    from doeff_claude_code._lines import subagent_transcript_of as subagent_transcript_of
    from doeff_claude_code._lines import tap_lines as tap_lines
    from doeff_claude_code._lines import text_at as text_at
    from doeff_claude_code._lines import thinking_start_of as thinking_start_of
    from doeff_claude_code._lines import timed_phases_of as timed_phases_of
    from doeff_claude_code._lines import tool_answer_of as tool_answer_of
    from doeff_claude_code._lines import tool_start_of as tool_start_of
    from doeff_claude_code._lines import transcript_record as transcript_record
    from doeff_claude_code._lines import transcript_records as transcript_records
    from doeff_claude_code._lines import usage_at_mark as usage_at_mark
    from doeff_claude_code._lines import usage_of as usage_of
    from doeff_claude_code._lines import whole_lines_end as whole_lines_end
    from doeff_claude_code._lines import whole_ms_of as whole_ms_of

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_claude_code._lines", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_claude_code._lines")
