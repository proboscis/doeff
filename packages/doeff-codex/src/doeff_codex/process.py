# moved-submodule-shim: doeff_codex.process -> doeff_codex._process
# moved-submodule-shim-names doeff_codex.process:
#   CUT_HEAD_BYTES CodexProcess CutLine EOF_GRACE_SECONDS LINE_CAP_BYTES LOG MODULE_TAGS
#   READ_CHUNK_BYTES STDERR_TAIL_LINES StdinWriter TERM_GRACE_SECONDS
"""Moved: ``doeff_codex.process`` is now ``doeff_codex._process`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_codex.process…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_codex._process import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_codex._process", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_codex._process")
