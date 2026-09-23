"""Every function annotation in doeff_conductor must evaluate at runtime.

Since ``from __future__ import annotations`` was removed (doeff#481), Python
3.14 evaluates annotations lazily on first access. ``inspect.signature`` (used
by ``mock_handlers`` to detect ``(effect, k)`` handlers) touches them, so an
annotation naming a ``TYPE_CHECKING``-only import raises ``NameError`` and a
quoted forward reference in a union (``"X" | None``) raises ``TypeError`` —
only when that code path is inspected. This sweep makes the defect class
fail here instead of in a distant workflow test.
"""

import importlib
import inspect
import pkgutil
from collections.abc import Iterator
from types import ModuleType

import doeff_conductor


def _modules() -> Iterator[ModuleType]:
    for info in pkgutil.walk_packages(doeff_conductor.__path__, "doeff_conductor."):
        yield importlib.import_module(info.name)


def _functions(module: ModuleType) -> Iterator[object]:
    for value in vars(module).values():
        if getattr(value, "__module__", None) != module.__name__:
            continue
        members = [value, *vars(value).values()] if inspect.isclass(value) else [value]
        for member in members:
            function = getattr(member, "__func__", member)
            if inspect.isfunction(function):
                yield function


def _annotation_error(function: object) -> str | None:
    try:
        inspect.signature(function)
    except (NameError, TypeError) as error:
        return f"{function.__module__}.{function.__qualname__}: {error}"
    return None


def test_every_function_annotation_evaluates() -> None:
    failures: list[str] = [
        error
        for module in _modules()
        for function in _functions(module)
        if (error := _annotation_error(function)) is not None
    ]
    assert failures == []
