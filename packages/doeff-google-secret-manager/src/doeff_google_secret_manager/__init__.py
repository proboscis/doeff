"""Google Secret Manager integration for doeff."""
from importlib import import_module
from typing import TYPE_CHECKING

from doeff_secret import DeleteSecret, GetSecret, ListSecrets, SetSecret

from ._client import SecretManagerClient, get_secret_manager_client
from ._handlers import mock_handlers, production_handlers
from ._secrets import access_secret

__all__ = [
    "DeleteSecret",
    "GetSecret",
    "ListSecrets",
    "SecretEffectBase",
    "SecretManagerClient",
    "SetSecret",
    "access_secret",
    "get_secret_manager_client",
    "mock_handlers",
    "production_handlers",
]


# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers. A root that already has `__all__`
# lists the names there too; the tool does not add `__all__` to a root without one (DOEFF021).
if TYPE_CHECKING:
    from doeff_google_secret_manager._effects import SecretEffectBase as SecretEffectBase

_LAZY_EXPORTS: dict[str, str] = {
    "SecretEffectBase": "doeff_google_secret_manager._effects",
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
