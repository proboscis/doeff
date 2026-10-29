# moved-submodule-shim: doeff_claude_code.handler -> doeff_claude_code._handler
# moved-submodule-shim-names doeff_claude_code.handler:
#   AGENT_ID_SHAPE AgentToolResult Answered AttachmentRefused BackendLost Binding CLI_TIMING_LOG
#   COLD_RESUME_TIMEOUT_SECONDS COST_TAIL_BYTES CallOpened CarryRefused ClaudeAnswerPermission
#   ClaudeCloseSession ClaudeCodeHost ClaudeDropProcess ClaudeEmitOutsideTurn ClaudeExportSession
#   ClaudeHome ClaudeInjectInput ClaudeInterruptTurn ClaudeLiveLimitExceeded ClaudeLiveProcess
#   ClaudeProcess ClaudeReadTurnEvents ClaudeReplaceSessionCredential ClaudeSessionSpec
#   ClaudeSessionStatus ClaudeStartTurn ClaudeStreamLine ClaudeTurn ClaudeWarmSession Closed
#   ClosedSession Completed CostMark CredentialReplaced DeltaKind DeltaTally DialogueState Doorbell
#   EOF_GRACE_SECONDS FRESH_MARK Failed FirstTextLines ForkSession FreshSession
#   HomeCredentialSession IMAGE_MIMES Idle Init InputAlreadyRead InputQueued InterruptRequested
#   Interrupted KEPT_TURNS LIVE_LIMIT_LOG Launch LaunchFailed LaunchReady LentCredential
#   LinkFromHome LiveProcess NoLiveProcess NoSuchRequest NoSuchSession NoTurnInFlight PartialMessage
#   ProcessStillAlive RETIRE_WAIT_SECONDS ROTATE_ABOVE_BYTES Rebuilt Refuse RequestTiming
#   ResumeSession Reuse SUBAGENT_ANSWERED_STATUS SUBAGENT_REREAD_SECONDS SUBAGENT_SETTLE_SECONDS
#   SUBAGENT_TASK_TYPE SUBAGENT_TRANSCRIPT_LOG SessionClosed SessionExported SessionNotFound
#   SessionProcessDown SessionRuntime SessionStatus SessionView SessionWarmed StopReason
#   SubagentRead SubagentTask SubagentTranscript SubagentTranscriptProblem TERM_GRACE_SECONDS
#   TRANSCRIPT_ROTATION_LOG TRANSCRIPT_THREADS TaskEvent TimedPhase ToolResult TranscriptAbsent
#   TranscriptBytes TranscriptKept TranscriptPlace TranscriptPresent TranscriptRotated
#   TranscriptRotationFailed TranscriptUnrotatable TurnEndLines TurnEventPage TurnInput TurnLog
#   TurnResult TurnRunning TurnStarted UNSETTLED_PROBLEMS UnknownTurn add_subagent_lines
#   answer_permission apply_carry apply_transition await_init call_step claimed_first_reply
#   claimed_first_text claimed_turn_end classify_record claude_code_handler close_credential_home
#   close_session cold_resume_argv counted_live credential_due drop_process due_subagents elapsed_ms
#   emit_outside export_session file_present in_flight_log inject_input interrupt_turn launch_argv
#   launch_cost_mark launch_key launch_mark_of lent_credential_home link_if_present live_process
#   note_first_reply note_first_text note_init note_over_declared_limit note_over_limit note_reused
#   note_spawned note_subagent_task note_turn_end note_unread_subagents observed_view on_down
#   on_exit on_line open_credential_home page_of parse_record phases_text prepare_launch
#   process_cost_mark process_env process_of read_events read_session_export read_subagent
#   read_transcript_bytes read_transcript_from read_transcript_place read_transcript_state
#   recorded_cost recorded_cost_mark refused_attachment release_closed replace_credential
#   replace_session_credential request_timing_fields retire_idle retire_if_due retire_time
#   retire_under_floor reuse_turn rotate_before_resume rotate_transcript run_cold_resume
#   session_status session_view settled_subagent spawn_idle spawn_turn start_decision start_turn
#   startup_fields subagent_transcript_of subagent_transcript_path transcript_cost_mark
#   transcript_dir transcript_path transcript_place unrotatable_note wait_until wall_elapsed_ms
#   wall_ms_of warm_session
"""Moved: ``doeff_claude_code.handler`` is now ``doeff_claude_code._handler`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_claude_code.handler…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_claude_code._handler", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_claude_code._handler")
