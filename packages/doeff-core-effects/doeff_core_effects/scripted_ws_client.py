# moved-submodule-shim: doeff_core_effects.scripted_ws_client -> doeff_core_effects._scripted_ws_client
# moved-submodule-shim-names doeff_core_effects.scripted_ws_client:
#   MODULE_TAGS NOT_IN_SCRIPT_REASON ReadWsSent SCRIPTED_WS_EFFECTS SCRIPTED_WS_HANDLES
#   SCRIPT_EXHAUSTED_REASON ScriptedLink ScriptedWsBinary ScriptedWsClosed ScriptedWsEndpoint
#   ScriptedWsFrame ScriptedWsText WsBinary WsConnect WsConnectFailed WsDisconnect WsDisconnectAll
#   WsFrame WsLink WsLinkClosed WsReceive WsScript WsSend WsSent WsSentClose WsSentText WsText
#   closed_answer closing_links endpoint_for frame_of link_name link_of next_frame replies_for
#   scripted_ws_client scripted_ws_link_handler without_link
"""Moved:``doeff_core_effects.scripted_ws_client`` is now ``doeff_core_effects._scripted_ws_client`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.scripted_ws_client…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._scripted_ws_client import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._scripted_ws_client", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._scripted_ws_client")
