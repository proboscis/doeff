# moved-submodule-shim: doeff_claude_code.dialogue -> doeff_claude_code._dialogue
# moved-submodule-shim-names doeff_claude_code.dialogue:
#   ACCOUNT_REFUSAL_ERRORS ASSISTANT_ERROR_RATE_LIMIT AccountLimitHit AccountRefusalHit Allow
#   AssistantMessage BackendLost CLI_OWN_TURN_ORIGINS ClaudeLineKind Completed ControlResponse
#   CostMark Deny DialogueState Failed HookNotice INPUT_FATES INPUT_FATE_TERMINAL
#   INTERRUPT_RECEIPT_CAPABILITY ImageAttachment Init Injection InputFate Interrupted
#   LIFECYCLE_CAPABILITY ModelRefusalHit ModelWindow NoStop OUTSIDE_TURN_QUIET_HOOK_EVENTS
#   OUTSIDE_TURN_QUIET_KINDS PermissionRequested PrintContent RATE_LIMIT_REJECTED RateLimit
#   STOP_REASON_REFUSAL StopControl StopReason StopSignal Transition TurnInput TurnResult Usage
#   answer_permission begin_turn close_session closed_turn content_block dumps end_of_result ended
#   exited_end image_block inject input_already_read interrupt interrupt_request_line
#   interrupted_by_result knows_injection limit_hit_after mark_of_result merged_windows
#   model_refusal_after on_assistant on_control_response on_exit on_init on_lifecycle
#   on_permission_request on_rate_limit on_record on_result per_turn_model_usage
#   permission_response_line print_once_stdin queued_refs quiet_before_first_input
#   quiet_outside_turn refusal_hit_after result_input_refs set_fate stderr_tail_within user_line
#   with_last_call
"""Moved: ``doeff_claude_code.dialogue`` is now ``doeff_claude_code._dialogue`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_claude_code.dialogue…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_claude_code._dialogue", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_claude_code._dialogue")
