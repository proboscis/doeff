# moved-submodule-shim: doeff_claude_code.argv -> doeff_claude_code._argv
"""Moved: ``doeff_claude_code.argv`` is now ``doeff_claude_code._argv`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_claude_code.argv…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_claude_code._argv import DISABLE_ALL_HOOKS as DISABLE_ALL_HOOKS
    from doeff_claude_code._argv import OUTSIDE_TURN_TOOLS as OUTSIDE_TURN_TOOLS
    from doeff_claude_code._argv import PER_TURN_SETTINGS_ENV as PER_TURN_SETTINGS_ENV
    from doeff_claude_code._argv import PRINT_ONCE_CONTENT_FLAGS as PRINT_ONCE_CONTENT_FLAGS
    from doeff_claude_code._argv import PRINT_ONCE_FLAGS as PRINT_ONCE_FLAGS
    from doeff_claude_code._argv import PRINT_ONCE_ISOLATION_FLAGS as PRINT_ONCE_ISOLATION_FLAGS
    from doeff_claude_code._argv import STARTUP_TIMING_ENV as STARTUP_TIMING_ENV
    from doeff_claude_code._argv import STREAM_FLAGS as STREAM_FLAGS
    from doeff_claude_code._argv import AskHost as AskHost
    from doeff_claude_code._argv import AutocompactAuto as AutocompactAuto
    from doeff_claude_code._argv import AutocompactTokens as AutocompactTokens
    from doeff_claude_code._argv import BypassAll as BypassAll
    from doeff_claude_code._argv import ClaudeHome as ClaudeHome
    from doeff_claude_code._argv import ClaudePrintOnce as ClaudePrintOnce
    from doeff_claude_code._argv import ClaudeSessionSpec as ClaudeSessionSpec
    from doeff_claude_code._argv import DenyUnlisted as DenyUnlisted
    from doeff_claude_code._argv import ForkSession as ForkSession
    from doeff_claude_code._argv import FreshSession as FreshSession
    from doeff_claude_code._argv import FrozenMap as FrozenMap
    from doeff_claude_code._argv import HomeSettings as HomeSettings
    from doeff_claude_code._argv import LentCredential as LentCredential
    from doeff_claude_code._argv import Mapping as Mapping
    from doeff_claude_code._argv import McpSse as McpSse
    from doeff_claude_code._argv import McpStdio as McpStdio
    from doeff_claude_code._argv import PrintContent as PrintContent
    from doeff_claude_code._argv import ResumeSession as ResumeSession
    from doeff_claude_code._argv import autocompact_flags as autocompact_flags
    from doeff_claude_code._argv import base_flags as base_flags
    from doeff_claude_code._argv import cold_resume_argv as cold_resume_argv
    from doeff_claude_code._argv import credential_key_of as credential_key_of
    from doeff_claude_code._argv import launch_argv as launch_argv
    from doeff_claude_code._argv import launch_flags as launch_flags
    from doeff_claude_code._argv import launch_key as launch_key
    from doeff_claude_code._argv import mangle_cwd as mangle_cwd
    from doeff_claude_code._argv import mcp_entry as mcp_entry
    from doeff_claude_code._argv import mcp_flags as mcp_flags
    from doeff_claude_code._argv import merged_settings as merged_settings
    from doeff_claude_code._argv import origin_flags as origin_flags
    from doeff_claude_code._argv import permission_flags as permission_flags
    from doeff_claude_code._argv import print_once_argv as print_once_argv
    from doeff_claude_code._argv import print_once_env as print_once_env
    from doeff_claude_code._argv import process_env as process_env
    from doeff_claude_code._argv import settings_flags as settings_flags
    from doeff_claude_code._argv import subagent_transcript_path as subagent_transcript_path
    from doeff_claude_code._argv import thaw_json as thaw_json
    from doeff_claude_code._argv import transcript_dir as transcript_dir
    from doeff_claude_code._argv import transcript_path as transcript_path

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_claude_code._argv", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_claude_code._argv")
