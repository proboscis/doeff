# moved-submodule-shim: doeff_codex.handler -> doeff_codex._handler
# moved-submodule-shim-names doeff_codex.handler:
#   Answered BackendLost CLIENT_NAME CLIENT_VERSION CodexAnswerRequest CodexCloseSession CodexEvent
#   CodexHost CodexInput CodexInterruptTurn CodexLaunchCount CodexProcess CodexReadTurnEvents
#   CodexSessionSpec CodexStartTurn CodexSteerTurn CodexTurn Continuation CutLine Doorbell
#   EOF_GRACE_SECONDS EXIT_RECORD_SECONDS FreshThread InterruptRequested KEPT_TURNS LINE_CAP_BYTES
#   LaunchFailed MODULE_TAGS NOT_RUNNING_CODE NOT_RUNNING_WORDS NoSuchRequest NoTurnInFlight
#   ProcessStillAlive RETIRE_WAIT_SECONDS RequestRefused ResumeThread SessionClosed SessionRuntime
#   StateDbHandoff Steered TERM_GRACE_SECONDS ThreadUnknown ToolsUnavailable TurnEventPage
#   TurnInFlight TurnLog TurnStarted UnknownTurn absorb another_thread answer_request answered
#   app_server_argv claim_state_db_within close_credential_home close_session codex_handler
#   credential_env exit_recorded image_urls_of initialize_request initialized_notification
#   interrupt_turn launch launch_count launch_failure merged_limits on_exit on_line
#   open_credential_home page_of page_ready process_down read_events refused repoint_in_background
#   repoint_state_db request retire server_response_line settle_read shed start_repoint start_turn
#   state_db_claim state_db_release steer_refused_as_not_running steer_turn take_state_db_lock
#   thread_resume_request thread_start_request turn_interrupt_request turn_start_request
#   turn_steer_request usage_limit_hit_of wait_until
"""Moved: ``doeff_codex.handler`` is now ``doeff_codex._handler`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_codex.handler…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_codex._handler import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_codex._handler", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_codex._handler")
