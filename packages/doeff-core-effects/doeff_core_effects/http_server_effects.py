# moved-submodule-shim: doeff_core_effects.http_server_effects -> doeff_core_effects._http_server_effects
# moved-submodule-shim-names doeff_core_effects.http_server_effects:
#   AppendHttpScript DEFAULT_DRAIN_SECONDS DEFAULT_WS_MAX_BYTES DEFAULT_WS_SEND_MAX_BYTES
#   FLUSH_SAMPLES_LIMIT HttpAddress HttpBody HttpBodyBytes HttpBodyFailed HttpBodyFileRange
#   HttpBodyOutcome HttpBodyRead HttpBodyTooLarge HttpCommand HttpEvent HttpForward HttpHeader
#   HttpListen HttpNextRequest HttpNoBody HttpProbe HttpProbeAnswer HttpReadBody HttpRequestArrived
#   HttpRespond HttpScript HttpServed HttpServerClosed HttpShutdown HttpStopListening HttpTakeQueued
#   ReadHttpServed ScriptedBody ScriptedUpstream TakeWsSendReport WS_CLOSE_ABNORMAL WS_CLOSE_NORMAL
#   WS_CUT_REASON WS_REFUSAL_TEXT WsAccept WsBinaryArrived WsClose WsCloseSent WsClosed
#   WsEvent WsForward WsOpened WsSendReport WsSendText WsSendTextEach WsShortcut WsShortcutAnswered
#   WsShortcutFailed WsTextArrived WsTextSent carries_content closing_of probe_answer probe_failure
#   probe_for send_overflows ws_refusal_status ws_shortcut_for ws_shortcut_outcome
"""Moved:``doeff_core_effects.http_server_effects`` is now ``doeff_core_effects._http_server_effects`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.http_server_effects…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._http_server_effects import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._http_server_effects", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._http_server_effects")
