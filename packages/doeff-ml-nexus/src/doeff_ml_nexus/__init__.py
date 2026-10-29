"""doeff-ml-nexus — 依存の転送・Docker の image の組み立て・Program の container での実行を effect と handler で表す。"""

from importlib import import_module

import hy  # noqa: F401 — enable Hy module imports

# Explicit submodule imports for doeff CLI discovery
from doeff_ml_nexus import _runner as runner  # noqa: F401

# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. The submodules are Hy modules without a `.pyi`, so no `X as X` for type
# checkers; `__all__` is not used (DOEFF021).
_LAZY_EXPORTS: dict[str, str] = {
    "Resolve": "doeff_ml_nexus._effects",
    "RsyncTo": "doeff_ml_nexus._effects",
    "WriteFile": "doeff_ml_nexus._effects",
    "docker_run_handler": "doeff_ml_nexus._handlers.docker",
    "resolve_handler": "doeff_ml_nexus._handlers.resolve",
    "rsync_handler": "doeff_ml_nexus._handlers.rsync",
    "write_file_handler": "doeff_ml_nexus._handlers.file",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
