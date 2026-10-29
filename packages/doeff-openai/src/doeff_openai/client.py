# moved-submodule-shim: doeff_openai.client -> doeff_openai._client
# moved-submodule-shim-names doeff_openai.client:
#   APICallMetadata CalculateCost ClientHolder OpenAIClient TokenUsage extract_request_id
#   extract_token_usage get_api_calls get_model_cost get_openai_client get_total_cost
#   reset_cost_tracking track_api_call
"""Moved: ``doeff_openai.client`` is now ``doeff_openai._client`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_openai.client…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_openai._client import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_openai._client", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_openai._client")
