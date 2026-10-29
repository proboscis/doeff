# moved-submodule-shim: doeff_agents.handlers -> doeff_agents._handlers
# moved-submodule-shim-names doeff_agents.handlers:
#   AgentSessionRepository AgentdSessionClient ClaudeRuntimePolicy CodexRuntimePolicy
#   MockSessionScript ScenarioStep ScenarioWorld SessionBackend agent_effectful_handler
#   agent_effectful_handlers claude_agent_adapter_handler claude_agent_handler
#   claude_agent_runtime_handlers claude_process_layer_handler codex_agent_adapter_handler
#   codex_agent_handler codex_process_layer_handler daemon_agent_handler daemon_agent_handlers
#   fake_claude_agent_runtime_handlers fake_claude_process_layer_handler
#   fake_codex_process_layer_handler mock_agent_handler mock_agent_handlers scenario_agent_handler
# moved-submodule-shim-names doeff_agents.handlers.claude:
#   AgentCapabilityUnsupportedError AgentType CaptureEffect ClaudeAdapter LaunchEffect LaunchParams
#   McpToolServer MonitorEffect MonitorState Observation SendEffect SessionBackend SessionHandle
#   SessionStatus StopEffect WarmSessionEffect assert_session_env_is_non_auth_overlay claude_handler
#   deliver_prompt_when_ready detect_status hash_content is_waiting_for_input mcp_server_loop
#   refuse_turn_capabilities wrap_with_shell_exports
# moved-submodule-shim-names doeff_agents.handlers.codex:
#   AgentCapabilityUnsupportedError AgentType CaptureEffect CodexAdapter ExportContextEffect
#   LaunchEffect LaunchParams MonitorEffect MonitorState Observation SendEffect SessionBackend
#   SessionHandle SessionStatus StopEffect WarmSessionEffect assert_session_env_is_non_auth_overlay
#   codex_handler deliver_prompt_when_ready detect_status hash_content is_waiting_for_input
#   refuse_turn_capabilities wrap_with_shell_exports
# moved-submodule-shim-names doeff_agents.handlers.daemon:
#   AgentEffect AgentError AgentLaunchError AgentNotAvailableError AgentSessionLifecycle
#   AgentSessionSnapshot AgentType AgentdClientError AgentdSessionClient AttachAgentSessionEffect
#   AwaitOutcome AwaitResultEffect CancelAgentSessionEffect CaptureEffect ClaudeLaunchEffect
#   ClaudeRuntimePolicy CleanupAgentSessionEffect CodexRuntimePolicy FollowUpEffect
#   GetAgentSessionEffect IoRoot LaunchEffect LaunchParams LaunchSessionEffect
#   ListAgentSessionsEffect MonitorEffect Observation ObserveAgentSessionEffect
#   RPC_ERR_NO_SUCH_SESSION ReleaseSessionEffect SendEffect SessionHandle SessionNotFoundError
#   StopEffect StopSessionEffect WarmSessionEffect assert_no_forbidden_agent_env
#   assert_session_env_is_non_auth_overlay get_adapter prepare_claude_home refuse_turn_capabilities
#   wrap_with_shell_exports
# moved-submodule-shim-names doeff_agents.handlers.effectful:
#   AWAIT_RESULT_AWAITING_INPUT_STABILITY_SECONDS AWAIT_RESULT_POLL_INTERVAL_SECONDS
#   AgentCapabilityUnsupportedError AgentEffect AttachAgentSessionEffect AwaitResultEffect
#   AwaitStatus CancelAgentSessionEffect CaptureEffect ClaudeLaunchEffect CleanupAgentSessionEffect
#   DEFAULT_AWAIT_BUDGET_SECONDS FollowUpEffect GetAgentSessionEffect LaunchEffect
#   LaunchSessionEffect ListAgentSessionsEffect McpToolServer MonitorEffect
#   ObserveAgentSessionEffect ReleaseSessionEffect SendEffect SessionBackend StopEffect
#   StopSessionEffect WarmSessionEffect
#   make_report_result_tool mcp_server_loop spec_uses_report_result_transport tmux_agent_defhandler
# moved-submodule-shim-names doeff_agents.handlers.headless:
#   API_STATUS_OVERLOADED AgentAccountLimit AgentAccountRefusal AgentCallUsageEvent
#   AgentCapabilityUnsupportedError AgentCompactionEndedEvent AgentCompactionEvent
#   AgentCompactionStartedEvent AgentError AgentEventPage AgentHookEvent AgentInputFateEvent
#   AgentLaunchError AgentModelRefusal AgentProviderCapacity AgentSessionLifecycle
#   AgentStopHookFeedbackEvent AgentSubagentCallUsageEvent AgentTextDeltaEvent AgentTextEvent
#   AgentThinkingDeltaEvent AgentThinkingStartedEvent AgentToolCallStartedEvent
#   AgentToolInputDeltaEvent AgentToolResultEvent AgentToolUseEvent AgentTurnCompleted
#   AgentTurnEndEvent AgentTurnFailed AgentTurnInterrupted AgentTurnLost AgentTurnUsage AgentType
#   AttachAgentSessionEffect AwaitOutcome AwaitResultEffect AwaitStatus CaptureEffect
#   CompactionOutcome CompactionTrigger EventsEffect ExportContextEffect FollowUpEffect
#   GITHUB_TOKEN_ENV HANDLER_NAME HandlerMadeContextId HeadlessClaudeConfig HeadlessSession
#   HeadlessState HomeTurnCredential InputFateState InterruptEffect LaunchEffect MonitorEffect
#   NamedContextId NoTurnInFlightError Observation RedeemTurnCredentialEffect ReleaseSessionEffect
#   ReplaceSessionCredentialEffect ReplacementCredentialUnavailable ResumeTargetNotFoundError
#   SendEffect SessionAlreadyExistsError SessionCredentialReplaced SessionHandle
#   SessionNotFoundError SessionOnHomeCredential SessionProcessNotLive SessionStatus StopEffect
#   StopSessionEffect TurnCredential TurnCredentialUnavailable TurnCredentialUnavailableError
#   TurnInFlightError TurnInputMode WarmSessionEffect account_limit_of account_refusal_of
#   append_event assert_no_forbidden_agent_env assert_session_env_is_non_auth_overlay await_turn_end
#   call_usage_builders_of close_turn closed_call_builders_of deliver detail_of end_of
#   event_builders_of events_of export_context headless_claude_handler images_of interrupt
#   last_call_of launch launch_context_id lent_of model_refusal_of new_ref next_origin outcome_of
#   page_of provider_capacity_of pull read_events redeemed_credential refuse_keys replace_credential
#   replacement_credential spec_of start_failure start_refusal start_turn start_waiting status_of
#   stop subagent_call_builder subagent_call_usage_builders_of superseded_names turn_end_builder
#   usage_of warm
# moved-submodule-shim-names doeff_agents.handlers.headless_codex:
#   AgentAccountLimit AgentCallUsageEvent AgentCapabilityUnsupportedError AgentError AgentEventPage
#   AgentInputFateEvent AgentLaunchError AgentProviderCapacity AgentSessionLifecycle
#   AgentTextDeltaEvent AgentTextEvent AgentThinkingDeltaEvent AgentThinkingStartedEvent
#   AgentToolCallStartedEvent AgentToolResultEvent AgentToolUseEvent AgentTurnCompleted
#   AgentTurnEndEvent AgentTurnFailed AgentTurnInterrupted AgentTurnLost AgentTurnUsage AgentType
#   AttachAgentSessionEffect AwaitOutcome AwaitResultEffect AwaitStatus CaptureEffect
#   CodexHeadlessSession CodexTurnCredential EventsEffect ExportContextEffect FollowUpEffect
#   HANDLER_NAME HeadlessCodexConfig HeadlessCodexState HomeTurnCredential InputFateState
#   InterruptEffect LaunchEffect MODULE_TAGS MonitorEffect NamedContextId NoTurnInFlightError
#   Observation RedeemTurnCredentialEffect ReleaseSessionEffect ReplaceSessionCredentialEffect
#   ResumeTargetNotFoundError SendEffect SessionAlreadyExistsError SessionHandle
#   SessionNotFoundError SessionStatus StopEffect StopSessionEffect TurnCredential
#   TurnCredentialUnavailable TurnCredentialUnavailableError TurnInFlightError TurnInputMode
#   WINDOW_NAMES WaitingInput WarmSessionEffect absorb_record account_limit_of added append_event
#   assert_no_forbidden_agent_env assert_session_env_is_non_auth_overlay await_turn_end
#   call_usage_of close_turn compact_limit_of deliver end_of events_of headless_codex_handler
#   images_of interrupt launch new_ref next_origin outcome_of page_of provider_capacity_of pull
#   read_events redeemed_auth_json refuse_keys refuse_launch spec_of start_failure start_refusal
#   start_turn start_waiting status_of stop summed taken tool_answer_of tool_call_of tool_events_of
#   tool_input_of warm
# moved-submodule-shim-names doeff_agents.handlers.headless_compose:
#   HeadlessClaudeConfig HeadlessCodexConfig HeadlessCodexState HeadlessState claude_adapter
#   claude_process_layer codex_adapter codex_process_layer fake_claude_process_layer
#   fake_codex_process_layer fake_headless_claude_handlers headless_claude_handler
#   headless_claude_handlers headless_codex_handler
# moved-submodule-shim-names doeff_agents.handlers.mcp_server_loop:
#   McpToolRequest McpToolServer TOOL_VM_TIMEOUT mcp_server_loop run_tool_with_stack
# moved-submodule-shim-names doeff_agents.handlers.production:
#   AgentAdapter AgentAttemptExhaustedError AgentDeadlineExceededError AgentEffect
#   AgentLaunchError AgentNotAvailableError AgentReadyTimeoutError AgentSessionLifecycle
#   AgentSessionRepository AgentSessionSnapshot AgentSpec AgentTask AgentType
#   AgentValidationErrorKind AgentValidationFailure AttachAgentSessionEffect AwaitOutcome
#   AwaitResultEffect AwaitStatus CancelAgentSessionEffect CaptureEffect ClaudeAdapter
#   ClaudeLaunchEffect ClaudeRuntimePolicy CleanupAgentSessionEffect CodexAdapter CodexRuntimePolicy
#   DEFAULT_AWAIT_BUDGET_SECONDS FollowUpEffect GeminiAdapter GetAgentSessionEffect
#   InMemoryAgentSessionRepository InjectionMethod IoRoot LaunchEffect LaunchParams
#   LaunchSessionEffect ListAgentSessionsEffect MonitorEffect MonitorState Observation
#   ObserveAgentSessionEffect REPORT_RESULT_TOOL_NAME ReleaseSessionEffect SendEffect
#   SessionAlreadyExistsError SessionBackend SessionHandle SessionNotFoundError SessionState
#   SessionStatus StopEffect StopSessionEffect assert_no_forbidden_agent_env
#   assert_session_env_is_non_auth_overlay deliver_prompt_when_ready detect_status evolve_status
#   get_adapter hash_content is_waiting_for_input make_report_result_tool prepare_claude_home
#   refuse_turn_capabilities register_adapter spec_uses_report_result_transport
#   trust_workspace_in_codex_home validate_result_payload wrap_with_shell_exports
# moved-submodule-shim-names doeff_agents.handlers.session_store:
#   AgentSessionQuery AgentSessionSnapshot DEFAULT_TABLE GetAgentSessionEffect
#   ListAgentSessionsEffect PUT_EVENT PutAgentSessionEffect SqlAgentSessionRepository SqlDialect
#   agent_session_store checked_table_name row_to_snapshot select_many_statement
#   select_one_statement session_table_ddl upsert_statement
# moved-submodule-shim-names doeff_agents.handlers.testing:
#   AgentEffect AgentSessionLifecycle AgentSessionRepository AgentSessionSnapshot AgentType
#   AttachAgentSessionEffect AwaitOutcome AwaitResultEffect AwaitStatus CancelAgentSessionEffect
#   CaptureEffect ClaudeLaunchEffect CleanupAgentSessionEffect FollowUpEffect GetAgentSessionEffect
#   InMemoryAgentSessionRepository LaunchEffect LaunchSessionEffect ListAgentSessionsEffect
#   MockAgentState MockSessionScript MonitorEffect Observation ObserveAgentSessionEffect
#   ReleaseSessionEffect ScenarioStep ScenarioWorld SendEffect SessionAlreadyExistsError
#   SessionHandle SessionNotFoundError SessionStatus StopEffect StopSessionEffect WarmSessionEffect
#   refuse_turn_capabilities
"""Moved: ``doeff_agents.handlers`` is now ``doeff_agents._handlers`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_agents.handlers…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import importlib.abc
import importlib.util
import sys
from collections.abc import Sequence
from importlib import import_module
from importlib.machinery import ModuleSpec
from types import CodeType, ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_agents._handlers import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

_OLD = "doeff_agents.handlers"
_NEW = "doeff_agents._handlers"


class _MovedSubmodules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Answers ``<old>.<sub>`` with the ``<new>.<sub>`` module itself (one module, not a second copy)."""

    def find_spec(
        self, fullname: str, path: Sequence[str] | None = None, target: ModuleType | None = None
    ) -> ModuleSpec | None:
        if not fullname.startswith(_OLD + ".") or importlib.util.find_spec(self._target(fullname)) is None:
            return None
        return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec: ModuleSpec) -> None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        sys.modules[module.__name__] = import_module(self._target(module.__name__))

    def get_code(self, fullname: str) -> CodeType:
        # python -m <old>.<sub>: run the moved module as __main__.
        target = self._target(fullname)
        source = f"import runpy\nrunpy.run_module({target!r}, run_name='__main__', alter_sys=True)\n"
        return compile(source, f"<moved {fullname}>", "exec")

    @staticmethod
    def _target(fullname: str) -> str:
        return _NEW + fullname[len(_OLD) :]


if not any(isinstance(finder, _MovedSubmodules) for finder in sys.meta_path):
    sys.meta_path.insert(0, _MovedSubmodules())

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_agents._handlers", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_agents._handlers")
