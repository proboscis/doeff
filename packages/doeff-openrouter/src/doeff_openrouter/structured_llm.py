# moved-submodule-shim: doeff_openrouter.structured_llm -> doeff_openrouter._structured_llm
# moved-submodule-shim-names doeff_openrouter.structured_llm:
#   build_messages build_response_format_payload ensure_strict_schema process_structured_response
#   process_unstructured_response structured_llm
"""Moved: ``doeff_openrouter.structured_llm`` is now ``doeff_openrouter._structured_llm`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_openrouter.structured_llm…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_openrouter._structured_llm import *  # noqa: F403  # nosemgrep: doeff-no-star-imports


def _keep_entry_name() -> None:
    """Keeps the package's own ``structured_llm`` when the import system binds this old path on it.

    The package exports a ``structured_llm`` that is not this module. Before the move the package
    imported the child first and then rebound the name, so ``from doeff_openrouter import structured_llm``
    kept giving the package's own object; this keeps it so.
    """
    package = sys.modules["doeff_openrouter"]

    class _KeepEntryName(type(package)):
        def __setattr__(self, name: str, value: object) -> None:
            if name == "structured_llm" and isinstance(value, ModuleType):
                return
            super().__setattr__(name, value)

    package.__class__ = _KeepEntryName


_keep_entry_name()

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_openrouter._structured_llm", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_openrouter._structured_llm")
