# moved-submodule-shim: doeff_records.admission -> doeff_records._admission
# moved-submodule-shim-names doeff_records.admission:
#   Admitted AppendNew AppendReplay ByKeySuffix Conflict Event ExpectAbsent ExpectAny ExpectVersion
#   KeepFor Missing NotIndexed RecordsSchema Refused Row RowsConflict
#   RowsRefused StreamDecl TableDecl Written WrittenRows body_digest canonical_json epoch_ms
#   expect_form hyx_descending_valueXquestion_markX hyx_event_expiredXquestion_markX
#   hyx_json_equalXquestion_markX hyx_row_expiredXquestion_markX hyx_row_matchesXquestion_markX
#   hyx_row_matches_anyXquestion_markX hyx_terminal_rowXquestion_markX json_bytes judge_append
#   judge_expect judge_put judge_put_rows judge_write_replay judged_diff key_from_text key_head_of
#   key_head_refusal key_text landed_value listed_row next_watch_sequence projected replayed_answer
#   retention_cutoff_ms retention_group_of shape_refusal size_refusal state_of state_refusal
#   where_refusal write_digest
"""Moved: ``doeff_records.admission`` is now ``doeff_records._admission`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_records.admission…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_records._admission import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_records._admission", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_records._admission")
