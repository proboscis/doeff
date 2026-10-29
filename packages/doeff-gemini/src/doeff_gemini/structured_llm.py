# moved-submodule-shim: doeff_gemini.structured_llm -> doeff_gemini._structured_llm
# moved-submodule-shim-names doeff_gemini.structured_llm:
#   FileUriContentPart GeminiContentPart LocalFileContentPart TextContentPart UriContentPart
#   build_contents build_generation_config edit_image__gemini image_edit__gemini
#   process_image_edit_response process_structured_response process_unstructured_response
#   structured_llm__gemini
"""Moved: ``doeff_gemini.structured_llm`` is now ``doeff_gemini._structured_llm`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_gemini.structured_llm…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_gemini._structured_llm import *  # noqa: F403

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_gemini._structured_llm", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_gemini._structured_llm")
