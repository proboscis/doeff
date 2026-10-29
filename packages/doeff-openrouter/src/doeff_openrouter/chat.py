# moved-submodule-shim: doeff_openrouter.chat -> doeff_openrouter._chat
# moved-submodule-shim-names doeff_openrouter.chat:
#   OpenRouterResponseError chat_completion
"""Moved: ``doeff_openrouter.chat`` is now ``doeff_openrouter._chat`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_openrouter.chat…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_openrouter._chat import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_openrouter._chat", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_openrouter._chat")
