# moved-submodule-shim: doeff_core_effects.aiohttp_http_server -> doeff_core_effects._aiohttp_http_server
# moved-submodule-shim-names doeff_core_effects.aiohttp_http_server:
#   ABNORMAL_CLOSE_CODES Arrivals Await BODY_CHUNK_BYTES CONNECT_SECONDS Carried
#   CreateExternalPromise DEFAULT_DRAIN_SECONDS EDGE_KEY EDGE_LOOPS EDGE_LOOP_LOCK FILE_CHUNK_BYTES
#   FLUSH_SAMPLES_LIMIT HOP_BY_HOP HTTP_READ_SECONDS HttpAddress HttpBodyBytes HttpBodyFailed
#   HttpBodyFileRange HttpBodyOutcome HttpBodyRead HttpBodyTooLarge HttpCommand HttpEvent
#   HttpForward HttpHeader HttpListen HttpNextRequest HttpNoBody HttpProbe HttpReadBody
#   HttpRequestArrived HttpRespond HttpServerClosed HttpShutdown HttpStopListening HttpTakeQueued
#   IDLE_CONNECTION_SECONDS MODULE_TAGS NORMAL_CLOSE NO_STATUS_CLOSE PREFETCH_BYTES
#   RELAY_FAILED_CLOSE TakeWsSendReport WS_CLOSE_ABNORMAL WS_CUT_REASON WS_HANDSHAKE_PREFIX
#   WS_REFUSAL_TEXT WS_UPSTREAM_CONNECT_SECONDS WS_UPSTREAM_HANDSHAKE_SECONDS Wait WebEdge WsAccept
#   WsBinaryArrived WsClose WsCloseFrame WsClosed WsForward WsOpened WsPeer WsSendReport WsSendText
#   WsSendTextEach WsShortcutAnswered WsShortcutFailed WsTextArrived aiohttp_http_server
#   carries_content closing_of forwarded_headers hop_names next_on_edge probe_answer probe_for pump
#   read_on_edge refusal_status relay_failed send_overflows sendable_close upgrade_asked
#   ws_refusal_status ws_shortcut_for ws_shortcut_outcome
"""Moved:``doeff_core_effects.aiohttp_http_server`` is now ``doeff_core_effects._aiohttp_http_server`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.aiohttp_http_server…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._aiohttp_http_server import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._aiohttp_http_server", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._aiohttp_http_server")
