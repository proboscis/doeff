# moved-submodule-shim: doeff_agents.tmux -> doeff_agents._tmux
# moved-submodule-shim-names doeff_agents.tmux:
#   ANSI_PATTERN CONFIRM_CAPTURE_LINES CONFIRM_INITIAL_SECONDS CONFIRM_RETRY_WAITS IoGenerator
#   IoRoot PASTE_SETTLE_SECONDS SessionAlreadyExistsError SessionBackend SessionConfig SessionInfo
#   SessionNotFoundError StableTmuxSessionBackend TRANSCRIPT_DIR_MODE TRANSCRIPT_DIR_NAME
#   TRANSCRIPT_FILE_MODE TmuxError TmuxNotAvailableError TmuxSessionBackend as_bool as_int
#   as_optional_str as_process_outcome as_str as_str_tuple assert_no_forbidden_agent_env
#   attach_session attach_session_program capture_pane capture_pane_program
#   capture_transcript_program confirm_literal_prompt_submitted_program env_value
#   get_default_backend has_session has_session_program inside_session_program is_inside_tmux
#   is_tmux_available kill_session kill_session_program list_sessions list_sessions_program
#   make_dirs new_session new_session_argv new_session_program output_has_unsubmitted_paste_input
#   parse_session_list paste_buffer_name paste_literal_program process_id read_text require_success
#   run_process safe_name_part send_keys send_keys_program start_transcript_pipe_program strip_ansi
#   tail_text_lines temp_root tmux_argv tmux_available_program touch_file transcript_dir_program
#   transcript_file_name
"""Moved: ``doeff_agents.tmux`` is now ``doeff_agents._tmux`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_agents.tmux…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_agents._tmux import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_agents._tmux", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_agents._tmux")
