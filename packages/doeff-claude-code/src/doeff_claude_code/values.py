# moved-submodule-shim: doeff_claude_code.values -> doeff_claude_code._values
# moved-submodule-shim-names doeff_claude_code.values:
#   ASK_HOST_MODES AUTOCOMPACT_MAX_TOKENS AUTOCOMPACT_MIN_TOKENS Allow AskHost AutocompactAuto
#   AutocompactTokens AutocompactWindow BypassAll CREDENTIAL_FORBIDDEN_CHARS ClaudeHome
#   ClaudeSessionSpec ClaudeTurn Deny DenyUnlisted ForkSession FreshSession HomeSettings IMAGE_MIMES
#   ImageAttachment LentCredential LinkFromHome McpServer McpSse McpStdio PermissionAnswer
#   PermissionPolicy PrintContent Rebuilt ResumeSession SessionOrigin TurnInput
#   checked_env checked_session_id
"""Moved: ``doeff_claude_code.values`` is now ``doeff_claude_code._values`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_claude_code.values…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_claude_code._values import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_claude_code._values", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_claude_code._values")
