# moved-submodule-shim: doeff_core_effects.storage -> doeff_core_effects._storage
# moved-submodule-shim-names doeff_core_effects.storage:
#   DurableStorage InMemoryStorage SQLiteStorage is_program
# moved-submodule-shim-names doeff_core_effects.storage.memory:
#   Await InMemoryStorage
# moved-submodule-shim-names doeff_core_effects.storage.sqlite:
#   Await SQLiteStorage
"""Moved: ``doeff_core_effects.storage`` is now ``doeff_core_effects._storage`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.storage…`` runs the moved module. Deleted by
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
    from doeff_core_effects._storage import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

_OLD = "doeff_core_effects.storage"
_NEW = "doeff_core_effects._storage"


class _MovedSubmodules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Answers ``<old>.<sub>`` with the ``<new>.<sub>`` module itself (one module, not a second copy)."""

    def find_spec(
        self, fullname: str, path: Sequence[str] | None = None, target: ModuleType | None = None
    ) -> ModuleSpec | None:
        if not fullname.startswith(_OLD + "."):
            return None
        moved = importlib.util.find_spec(self._target(fullname))
        if moved is None:
            return None
        # The origin is the moved file: python -m <old>.<sub> runs with __file__ and sys.argv[0] naming it (a __main__
        # without a file breaks inspect.getmodule, which Hy's compile and doeff-hy's bytecode guard call).
        return importlib.util.spec_from_loader(fullname, self, origin=moved.origin)

    def create_module(self, spec: ModuleSpec) -> None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        sys.modules[module.__name__] = import_module(self._target(module.__name__))

    def get_code(self, fullname: str) -> CodeType | None:
        # python -m <old>.<sub>: run the moved module's own code as __main__ (a package's __main__, as runpy does). Not a
        # nested runpy.run_module: its outer run leaves a __main__ without a file in sys.modules while the moved Hy module
        # compiles, and inspect.getmodule raises TypeError on it.
        target = self._target(fullname)
        moved = importlib.util.find_spec(target)
        if moved is not None and moved.submodule_search_locations is not None:
            moved = importlib.util.find_spec(target + ".__main__")
        if moved is None or not isinstance(moved.loader, importlib.abc.InspectLoader):
            raise ImportError(f"no code to run for the moved module {target}", name=fullname)
        return moved.loader.get_code(moved.name)

    @staticmethod
    def _target(fullname: str) -> str:
        return _NEW + fullname[len(_OLD) :]


if not any(isinstance(finder, _MovedSubmodules) for finder in sys.meta_path):
    sys.meta_path.insert(0, _MovedSubmodules())

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._storage", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._storage")
