# moved-submodule-shim: doeff_conductor.templates -> doeff_conductor._templates
# moved-submodule-shim-names doeff_conductor.templates:
#   TEMPLATES basic_pr enforced_pr get_available_templates get_template get_template_source
#   is_template multi_agent reviewed_pr
# moved-submodule-shim-names doeff_conductor.templates.basic_pr:
#   IMPLEMENT_SCHEMA Issue PRHandle basic_pr
# moved-submodule-shim-names doeff_conductor.templates.enforced_pr:
#   IMPLEMENT_SCHEMA Issue PRHandle TEST_SCHEMA enforced_pr
# moved-submodule-shim-names doeff_conductor.templates.multi_agent:
#   ARTIFACT_SCHEMA Issue PRHandle Workspace multi_agent
# moved-submodule-shim-names doeff_conductor.templates.reviewed_pr:
#   Agent AgentTask Commit CreatePR CreateWorkspace IMPLEMENT_SCHEMA Issue PRHandle Push
#   REVIEW_SCHEMA REVIEW_VERDICT_RESULT_SCHEMA ResolveIssue reviewed_pr
"""Moved: ``doeff_conductor.templates`` is now ``doeff_conductor._templates`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_conductor.templates…`` runs the moved module. Deleted by
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
    from doeff_conductor._templates import *  # noqa: F403

_OLD = "doeff_conductor.templates"
_NEW = "doeff_conductor._templates"


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

    runpy.run_module("doeff_conductor._templates", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_conductor._templates")
