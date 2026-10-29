"""doeff-seedream: ByteDance Seedream helpers for doeff."""

from importlib import import_module
from typing import TYPE_CHECKING

from doeff_image import ImageEdit, ImageGenerate, ImageResult

from ._costs import CostEstimate
from ._effects import SeedreamGenerate, SeedreamStructuredOutput
from ._handlers import mock_handlers, production_handlers, seedream_image_handler
from ._types import SeedreamImage, SeedreamImageEditResult

__all__ = [
    "CostEstimate",
    "ImageEdit",
    "ImageGenerate",
    "ImageResult",
    "MockSeedreamHandler",
    "SeedreamGenerate",
    "SeedreamImage",
    "SeedreamImageEditResult",
    "SeedreamStructuredOutput",
    "mock_handlers",
    "production_handlers",
    "seedream_image_handler",
]


# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers; `__all__` is not used (DOEFF021).
if TYPE_CHECKING:
    from doeff_seedream._handlers.testing import MockSeedreamHandler as MockSeedreamHandler

_LAZY_EXPORTS: dict[str, str] = {
    "MockSeedreamHandler": "doeff_seedream._handlers.testing",
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
