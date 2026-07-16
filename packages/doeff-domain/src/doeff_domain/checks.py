"""Opt-in conformance checks for declared effect domains."""

import importlib
import pkgutil
import sys
from collections.abc import Iterable
from types import ModuleType
from typing import Protocol, cast

from doeff_vm import EffectBase

from doeff_domain.handlers import handled_effects
from doeff_domain.registry import Domain, domain_for_effect


class _PackageModule(Protocol):
    __path__: Iterable[str]


def assert_domain_covered(domain: Domain) -> None:
    """Assert that declared handlers cover every effect introduced by a domain."""

    covered_effects: set[type[EffectBase]] = set()
    for handler in domain.handlers:
        covered_effects.update(handled_effects(handler, domain))
    missing_effects = tuple(
        effect_class for effect_class in domain.effects if effect_class not in covered_effects
    )
    if missing_effects:
        missing_names = ", ".join(
            f"{effect_class.__module__}.{effect_class.__qualname__}"
            for effect_class in missing_effects
        )
        raise AssertionError(
            f"domain {domain.name!r} handlers do not cover introduced effects: {missing_names}"
        )


def assert_no_orphan_effects(*, packages: Iterable[str | ModuleType]) -> None:
    """Import package scopes and assert every effect defined there has a domain owner."""

    modules: set[ModuleType] = set()
    for package_reference in packages:
        modules.update(_import_scope(package_reference))

    effect_classes: set[type[EffectBase]] = set()
    for module in modules:
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and value is not EffectBase
                and issubclass(value, EffectBase)
                and value.__module__ == module.__name__
            ):
                effect_classes.add(value)

    orphan_effects = tuple(
        sorted(
            (
                effect_class
                for effect_class in effect_classes
                if domain_for_effect(effect_class) is None
            ),
            key=lambda effect_class: (effect_class.__module__, effect_class.__qualname__),
        )
    )
    if orphan_effects:
        orphan_names = ", ".join(
            f"{effect_class.__module__}.{effect_class.__qualname__}"
            for effect_class in orphan_effects
        )
        raise AssertionError(f"effect classes without an introducing domain: {orphan_names}")


def _import_scope(package_reference: str | ModuleType) -> list[ModuleType]:
    if isinstance(package_reference, str):
        root_module = importlib.import_module(package_reference)
    elif isinstance(package_reference, ModuleType):
        root_module = package_reference
    else:
        raise TypeError(
            "assert_no_orphan_effects packages must contain module names or modules, "
            f"got {package_reference!r}"
        )

    root_name = root_module.__name__
    imported_modules: dict[str, ModuleType] = {root_name: root_module}
    if hasattr(root_module, "__path__"):
        package_paths = cast(_PackageModule, root_module).__path__
        for module_info in pkgutil.walk_packages(package_paths, prefix=f"{root_name}."):
            imported_module = importlib.import_module(module_info.name)
            imported_modules[imported_module.__name__] = imported_module

    prefix = f"{root_name}."
    for module_name, module in tuple(sys.modules.items()):
        if module is not None and (module_name == root_name or module_name.startswith(prefix)):
            imported_modules[module_name] = module
    return list(imported_modules.values())
