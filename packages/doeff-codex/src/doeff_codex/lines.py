# moved-submodule-shim: doeff_codex.lines -> doeff_codex._lines
# moved-submodule-shim-names doeff_codex.lines:
#   AgentCall AgentMessageDone AgentState AgentStateWire CODE_MODE_FAIL_CLOSED
#   CODE_MODE_UNAVAILABLE_HEAD CodeModeUnavailable CodexLine CodexWarning CollabAgentToolCallWire
#   CommandExecutionWire CommandRun DeltaParamsWire DynamicCall DynamicContentWire
#   DynamicToolCallWire EnvelopeWire ErrorFacts ErrorInfoWire ErrorKindNameWire ErrorParamsWire
#   ErrorResponse FileChangeWire FileEdit FilePatch FileUpdateWire HttpFailureWire IdWire ImageView
#   ImageViewWire ItemDone ItemParamsWire ItemStarted ItemWire JSON_HEAD_TOKEN LimitRecord
#   MODULE_TAGS McpCall McpContentWire McpErrorWire McpResultWire McpToolCallWire Other
#   PatchKindWire RateLimitSnapshotWire RateLimitWindow RateLimitWindowWire RateLimits
#   RateLimitsParamsWire ReasoningDelta Response ResultWire RpcErrorWire SERVER_OVERLOADED_KIND
#   ServerOverloadedHit ServerRequest TextDelta ThreadParamsWire ThreadStarted TokenCount
#   TokenCountWire TokenUsage TokenUsageParamsWire TokenUsageWire ToolItem ToolStatus TurnEnded
#   TurnError TurnErrorWire TurnParamsWire TurnRecord TurnStarted TurnStatus TurnWire
#   USAGE_LIMIT_KIND UnknownValue Unparsed UsageLimitHit WarningParamsWire WebSearch
#   WebSearchActionWire WebSearchWire agent_call_of classify_cut_line classify_line closed_json_head
#   command_run_of dynamic_call_of error_facts_of file_patch_of image_view_of item_done_of
#   item_started_of json_head_scan known_error_facts_of mcp_call_of notification_of parsed_into
#   rate_limits_of reason_of reasoning_summary_of reasoning_text_of response_of text_delta_of
#   thread_started_of token_count_of token_usage_of tool_of tool_parsed tool_status_of turn_ended_of
#   turn_error_of turn_started_of turn_status_of warning_of web_search_of window_of
"""Moved: ``doeff_codex.lines`` is now ``doeff_codex._lines`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_codex.lines…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_codex._lines import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_codex._lines", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_codex._lines")
