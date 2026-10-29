# moved-submodule-shim: doeff_core_effects.scripted_http_server -> doeff_core_effects._scripted_http_server
# moved-submodule-shim-names doeff_core_effects.scripted_http_server:
#   AppendHttpScript CLOSED_REASON DEFAULT_WS_SEND_MAX_BYTES EMPTY_REPORT FLUSH_SAMPLES_LIMIT
#   FileFailed HttpBodyBytes HttpBodyFailed HttpBodyFileRange HttpBodyOutcome HttpBodyRead
#   HttpBodyTooLarge HttpForward HttpHeader HttpListen HttpNextRequest HttpNoBody HttpProbe
#   HttpProbeAnswer HttpReadBody HttpRequestArrived HttpRespond HttpScript HttpServed
#   HttpServerClosed HttpShutdown HttpStopListening HttpTakeQueued MODULE_TAGS ProbeHit ReadBytes
#   ReadHttpServed ScriptSend ScriptTake ScriptedBody ScriptedUpstream TakeWsSendReport
#   WS_CUT_REASON WS_REFUSAL_TEXT WsAccept WsBinaryArrived WsClose WsCloseFrame WsCloseSent WsClosed
#   WsForward WsOpened WsSendReport WsSendText WsSendTextEach WsShortcut WsShortcutAnswered
#   WsTextArrived WsTextSent bodies_by_ticket body_text carries_content closing_of declared_length
#   probe_answer probe_for probe_hit probe_served script_sent script_take scripted_body_outcome
#   scripted_http_server send_overflows served_of tally_dropped tally_flushed tally_queued
#   upstream_for without_ticket ws_refusal_status ws_shortcut_for ws_shortcut_outcome
"""Moved:``doeff_core_effects.scripted_http_server`` is now ``doeff_core_effects._scripted_http_server`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.scripted_http_server…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._scripted_http_server import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._scripted_http_server", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._scripted_http_server")
