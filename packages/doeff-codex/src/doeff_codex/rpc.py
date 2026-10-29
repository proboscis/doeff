# moved-submodule-shim: doeff_codex.rpc -> doeff_codex._rpc
# moved-submodule-shim-names doeff_codex.rpc:
#   APP_SERVER_ARGS ApprovalPolicy ClientInfoWire ConfigWire ImageInputWire InitializeParamsWire
#   JSONRPC_VERSION MODULE_TAGS NotificationWire RequestParams RequestWire ResponseWire SandboxMode
#   TextInputWire ThreadResumeParamsWire ThreadStartParamsWire TurnInterruptParamsWire
#   TurnStartParamsWire TurnSteerParamsWire app_server_argv config_of initialize_request
#   initialized_notification input_wires_of request_line server_response_line thread_resume_request
#   thread_start_request turn_interrupt_request turn_start_request turn_steer_request
"""Moved: ``doeff_codex.rpc`` is now ``doeff_codex._rpc`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_codex.rpc…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_codex._rpc import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_codex._rpc", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_codex._rpc")
