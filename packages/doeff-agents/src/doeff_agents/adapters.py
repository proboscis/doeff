# moved-submodule-shim: doeff_agents.adapters -> doeff_agents._adapters
# moved-submodule-shim-names doeff_agents.adapters:
#   AgentAdapter AgentType ClaudeAdapter CodexAdapter GeminiAdapter InjectionMethod LaunchConfig
#   LaunchParams
# moved-submodule-shim-names doeff_agents.adapters.base:
#   AgentAdapter AgentSessionLifecycle AgentType InjectionMethod IoGenerator LaunchConfig
#   LaunchParams PreLaunchAdapter cli_available which_executable
# moved-submodule-shim-names doeff_agents.adapters.claude:
#   AgentType CLAUDE_SCREEN_READER_READY_PATTERN ClaudeAdapter InjectionMethod IoGenerator
#   LaunchParams MISSING_CLAUDE_JSON UNAUTHENTICATED_CLAUDE_JSON as_bool as_optional_str as_str
#   authenticated_account claude_pre_launch cli_available home_path logger make_dirs path_exists
#   read_text write_text
# moved-submodule-shim-names doeff_agents.adapters.codex:
#   AgentType CODEX_READY_PATTERN CodexAdapter InjectionMethod IoGenerator LaunchParams
#   as_optional_str cli_available codex_config_path make_dirs read_text toml_quoted_key
#   toml_quoted_string trust_workspace_in_codex_home trusted_config_text write_text
# moved-submodule-shim-names doeff_agents.adapters.gemini:
#   AgentType GeminiAdapter InjectionMethod LaunchParams cli_available
"""Moved: ``doeff_agents.adapters`` is now ``doeff_agents._adapters`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_agents.adapters…`` runs the moved module. Deleted by
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
    from doeff_agents._adapters import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

_OLD = "doeff_agents.adapters"
_NEW = "doeff_agents._adapters"


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

    runpy.run_module("doeff_agents._adapters", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_agents._adapters")
