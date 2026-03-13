"""Public handler entrypoints for doeff-openai effects."""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "MockOpenAIConfig": "doeff_openai.handlers.testing",
    "MockOpenAIState": "doeff_openai.handlers.testing",
    "mock_handlers": "doeff_openai.handlers.testing",
    "openai_mock_handler": "doeff_openai.handlers.testing",
    "openai_production_handler": "doeff_openai.handlers.production",
    "production_handlers": "doeff_openai.handlers.production",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
