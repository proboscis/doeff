# moved-submodule-shim: doeff_claude_code.print_once -> doeff_claude_code._print_once
# moved-submodule-shim-names doeff_claude_code.print_once:
#   AccountLimitHit ClaudePrintOnce DETAIL_CHARS PRINT_THREADS PrintAnswered PrintFailed
#   PrintLimited PrintProcess PrintTimedOut RanPrint TurnResult WORK_DIR_PREFIX classify_record
#   claude_print_handler close_credential_home elapsed_ms_since failed_detail limit_hit_after
#   limit_in_stream open_credential_home parse_record print_once print_once_argv print_once_env
#   print_once_stdin print_outcome run_print_once stderr_tail_within
"""Moved: ``doeff_claude_code.print_once`` is now ``doeff_claude_code._print_once`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_claude_code.print_once…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_claude_code._print_once import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_claude_code._print_once", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_claude_code._print_once")
