# moved-submodule-shim: doeff_core_effects.ws_client_effects -> doeff_core_effects._ws_client_effects
# moved-submodule-shim-names doeff_core_effects.ws_client_effects:
#   HttpHeader NOT_IN_SCRIPT_REASON ReadWsSent SCOPE_ENDED_REASON SCRIPT_EXHAUSTED_REASON
#   ScriptedWsBinary ScriptedWsClosed ScriptedWsEndpoint ScriptedWsFrame ScriptedWsReply
#   ScriptedWsText UNKNOWN_LINK_REASON WS_LINK_CLOSE_ABNORMAL WS_LINK_CLOSE_GOING_AWAY
#   WS_LINK_CLOSE_NORMAL WsBinary WsConnect WsConnectFailed WsConnectOutcome WsDisconnect
#   WsDisconnectAll WsFrame WsLink WsLinkClosed WsReceive WsScript WsSend WsSendOutcome WsSent
#   WsSentClose WsSentRecord WsSentText WsText closed_answer closing_links link_name
"""Moved:``doeff_core_effects.ws_client_effects`` is now ``doeff_core_effects._ws_client_effects`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.ws_client_effects…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._ws_client_effects import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._ws_client_effects", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._ws_client_effects")
