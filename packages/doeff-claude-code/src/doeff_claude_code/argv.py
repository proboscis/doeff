# moved-submodule-shim: doeff_claude_code.argv -> doeff_claude_code._argv
# moved-submodule-shim-names doeff_claude_code.argv:
#   AskHost AutocompactAuto AutocompactTokens BypassAll ClaudeHome ClaudePrintOnce ClaudeSessionSpec
#   DISABLE_ALL_HOOKS DenyUnlisted ForkSession FreshSession HomeSettings LentCredential McpSse
#   McpStdio OUTSIDE_TURN_TOOLS PER_TURN_SETTINGS_ENV PRINT_ONCE_CONTENT_FLAGS PRINT_ONCE_FLAGS
#   PRINT_ONCE_ISOLATION_FLAGS PrintContent ResumeSession STARTUP_TIMING_ENV STREAM_FLAGS
#   autocompact_flags base_flags cold_resume_argv credential_key_of launch_argv launch_flags
#   launch_key mangle_cwd mcp_entry mcp_flags merged_settings origin_flags permission_flags
#   print_once_argv print_once_env process_env settings_flags subagent_transcript_path
#   transcript_dir transcript_path
"""Moved: ``doeff_claude_code.argv`` is now ``doeff_claude_code._argv`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_claude_code.argv…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_claude_code._argv import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_claude_code._argv", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_claude_code._argv")
