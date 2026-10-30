"""doeff_run: the effects, values and handlers of the package, read on first use."""

from importlib import import_module

_LAZY_EXPORTS: dict[str, str] = {}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import hy  # noqa: F401 - the modules behind `_` are Hy modules; importing hy lets Python find them

    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
