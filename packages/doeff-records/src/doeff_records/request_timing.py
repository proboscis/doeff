# moved-submodule-shim: doeff_records.request_timing -> doeff_records._request_timing
# moved-submodule-shim-names doeff_records.request_timing:
#   LOCK_METRIC LOCK_PHASE_MEANINGS LockPhase MODULE_TAGS MarkAt OPERATIONS ROWS_LOCK_OPERATIONS
#   ROWS_UNIT RequestMark RequestStage SERVER_TIMING_NAMES SERVER_TIMING_STAGES STAGE_MEANINGS
#   STAGE_METRIC STAGE_METRIC_HELPS STAGE_METRIC_NAMES STREAM_LOCK_OPERATIONS ServerSpan
#   StageMeaning StageSeconds TABLE_LOCK_OPERATIONS WaitSum between first_at lock_metric
#   lock_metric_helps lock_observations metric_word request_stages sent_stages server_timing_of
#   server_timing_spans_of server_timing_spelled stage_metric stream_unit table_unit waited
"""Moved: ``doeff_records.request_timing`` is now ``doeff_records._request_timing`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_records.request_timing…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_records._request_timing import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_records._request_timing", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_records._request_timing")
