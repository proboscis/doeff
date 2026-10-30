# moved-submodule-shim: doeff_core_effects.aiohttp_ws_client -> doeff_core_effects._aiohttp_ws_client
# moved-submodule-shim-names doeff_core_effects.aiohttp_ws_client:
#   AIOHTTP_WS_EFFECTS AIOHTTP_WS_HANDLES Await CLOSE_SECONDS CONNECT_SECONDS HANDSHAKE_SECONDS
#   HttpHeader MAX_MESSAGE_BYTES MODULE_TAGS OpenLink WsBinary WsConnect WsConnectFailed
#   WsDisconnect WsDisconnectAll WsFrame WsLink WsLinkClosed WsReceive WsSend WsSent WsText
#   aiohttp_ws_client aiohttp_ws_link_handler close_link closed_answer closing_links drop_link
#   failure_detail link_name lost_code new_client_session open_link receive_frame send_text
"""Moved:``doeff_core_effects.aiohttp_ws_client`` is now ``doeff_core_effects._aiohttp_ws_client`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.aiohttp_ws_client…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._aiohttp_ws_client import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._aiohttp_ws_client", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._aiohttp_ws_client")
