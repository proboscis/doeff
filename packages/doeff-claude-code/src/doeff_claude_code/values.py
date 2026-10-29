# moved-submodule-shim: doeff_claude_code.values -> doeff_claude_code._values
"""Moved: ``doeff_claude_code.values`` is now ``doeff_claude_code._values`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_claude_code.values…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_claude_code._values import ASK_HOST_MODES as ASK_HOST_MODES
    from doeff_claude_code._values import AUTOCOMPACT_MAX_TOKENS as AUTOCOMPACT_MAX_TOKENS
    from doeff_claude_code._values import AUTOCOMPACT_MIN_TOKENS as AUTOCOMPACT_MIN_TOKENS
    from doeff_claude_code._values import CREDENTIAL_FORBIDDEN_CHARS as CREDENTIAL_FORBIDDEN_CHARS
    from doeff_claude_code._values import IMAGE_MIMES as IMAGE_MIMES
    from doeff_claude_code._values import Allow as Allow
    from doeff_claude_code._values import AskHost as AskHost
    from doeff_claude_code._values import AutocompactAuto as AutocompactAuto
    from doeff_claude_code._values import AutocompactTokens as AutocompactTokens
    from doeff_claude_code._values import AutocompactWindow as AutocompactWindow
    from doeff_claude_code._values import BypassAll as BypassAll
    from doeff_claude_code._values import ClaudeHome as ClaudeHome
    from doeff_claude_code._values import ClaudeSessionSpec as ClaudeSessionSpec
    from doeff_claude_code._values import ClaudeTurn as ClaudeTurn
    from doeff_claude_code._values import Deny as Deny
    from doeff_claude_code._values import DenyUnlisted as DenyUnlisted
    from doeff_claude_code._values import ForkSession as ForkSession
    from doeff_claude_code._values import FreshSession as FreshSession
    from doeff_claude_code._values import FrozenMap as FrozenMap
    from doeff_claude_code._values import HomeSettings as HomeSettings
    from doeff_claude_code._values import ImageAttachment as ImageAttachment
    from doeff_claude_code._values import LentCredential as LentCredential
    from doeff_claude_code._values import LinkFromHome as LinkFromHome
    from doeff_claude_code._values import McpServer as McpServer
    from doeff_claude_code._values import McpSse as McpSse
    from doeff_claude_code._values import McpStdio as McpStdio
    from doeff_claude_code._values import PermissionAnswer as PermissionAnswer
    from doeff_claude_code._values import PermissionPolicy as PermissionPolicy
    from doeff_claude_code._values import PrintContent as PrintContent
    from doeff_claude_code._values import Rebuilt as Rebuilt
    from doeff_claude_code._values import ResumeSession as ResumeSession
    from doeff_claude_code._values import SessionOrigin as SessionOrigin
    from doeff_claude_code._values import TranscriptCarry as TranscriptCarry
    from doeff_claude_code._values import TurnInput as TurnInput
    from doeff_claude_code._values import checked_env as checked_env
    from doeff_claude_code._values import checked_session_id as checked_session_id
    from doeff_claude_code._values import dataclass as dataclass
    from doeff_claude_code._values import field as field
    from doeff_claude_code._values import frozen_json_object as frozen_json_object
    from doeff_claude_code._values import frozen_map_of as frozen_map_of

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_claude_code._values", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_claude_code._values")
