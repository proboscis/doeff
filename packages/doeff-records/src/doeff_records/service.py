# moved-submodule-shim: doeff_records.service -> doeff_records._service
# moved-submodule-shim-names doeff_records.service:
#   ERROR_MALFORMED ERROR_NOT_FOUND ERROR_STORE_UNAVAILABLE HttpAnswer HttpRequest METHOD_GET
#   METHOD_POST MODULE_TAGS OPERATIONS PATH_HEALTHZ PATH_PREFIX Principal PublicEffect RecordsSchema
#   RecordsService STATUS_OF_ERROR Unreachable WireAnswer WireMalformed WireRefusal WireRequest
#   answer_reply decode_request hyx_off_loop_answerXquestion_markX json_answer keyed_request
#   off_loop records_service refusal_answer refusal_json reply_of respond sendable_reply_of
#   serve_operation undeclared_name undeclared_reason writer_of
"""Moved: ``doeff_records.service`` is now ``doeff_records._service`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_records.service…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_records._service import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_records._service", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_records._service")
