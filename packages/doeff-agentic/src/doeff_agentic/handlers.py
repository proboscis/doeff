# moved-submodule-shim: doeff_agentic.handlers -> doeff_agentic._handlers
# moved-submodule-shim-names doeff_agentic.handlers:
#   agentic_effectful_handlers mock_handlers opencode_handler production_handlers
#   recorded_workflows_handler tmux_handler
# moved-submodule-shim-names doeff_agentic.handlers.opencode:
#   opencode_handler
# moved-submodule-shim-names doeff_agentic.handlers.production:
#   agentic_effectful_handlers
# moved-submodule-shim-names doeff_agentic.handlers.recorded:
#   recorded_workflows_handler
# moved-submodule-shim-names doeff_agentic.handlers.testing:
#   mock_handlers
# moved-submodule-shim-names doeff_agentic.handlers.tmux:
#   tmux_handler
"""Moved: ``doeff_agentic.handlers`` is now ``doeff_agentic._handlers`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_agentic.handlers…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import importlib.abc
import importlib.util
import sys
from collections.abc import Sequence
from importlib import import_module
from importlib.machinery import ModuleSpec
from types import CodeType, ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_agentic._handlers import *  # noqa: F403

_OLD = "doeff_agentic.handlers"
_NEW = "doeff_agentic._handlers"


class _MovedSubmodules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Answers ``<old>.<sub>`` with the ``<new>.<sub>`` module itself (one module, not a second copy)."""

    def find_spec(
        self, fullname: str, path: Sequence[str] | None = None, target: ModuleType | None = None
    ) -> ModuleSpec | None:
        if not fullname.startswith(_OLD + ".") or importlib.util.find_spec(self._target(fullname)) is None:
            return None
        return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec: ModuleSpec) -> None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        sys.modules[module.__name__] = import_module(self._target(module.__name__))

    def get_code(self, fullname: str) -> CodeType:
        # python -m <old>.<sub>: run the moved module as __main__.
        target = self._target(fullname)
        source = f"import runpy\nrunpy.run_module({target!r}, run_name='__main__', alter_sys=True)\n"
        return compile(source, f"<moved {fullname}>", "exec")

    @staticmethod
    def _target(fullname: str) -> str:
        return _NEW + fullname[len(_OLD) :]


if not any(isinstance(finder, _MovedSubmodules) for finder in sys.meta_path):
    sys.meta_path.insert(0, _MovedSubmodules())

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_agentic._handlers", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_agentic._handlers")
