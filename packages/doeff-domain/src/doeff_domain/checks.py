"""Opt-in conformance checks (ADR-DOE-DOMAIN-001 D7).

Nothing here runs by default — adopting projects wire these functions as
their own tests. doeff itself adopts them for its own packages (dogfood).

- ``assert_domain_covered`` — check (a): the union of the domain handlers'
  handled sets must be a superset of the effects the domain *introduces*
  (included vocabulary is the introducing domain's responsibility).
- ``assert_no_orphan_effects`` — check (c): every ``EffectBase`` subclass
  defined at module level in the named packages must be introduced by some
  registered domain. Double introduction is already impossible at
  registration time (D3).

Both checks accept an explicit known-gap parameter with ratchet semantics:
a declared gap that is no longer real fails as stale, forcing its removal.
"""

import importlib
import pkgutil
import sys
from collections.abc import Iterable

from doeff_vm import EffectBase

from doeff_domain.introspect import handled_effects
from doeff_domain.registry import (
    Domain,
    DomainCheckError,
    DomainCoverageError,
    OrphanEffectError,
    get_domain,
    introducing_domain,
    registered_domains,
)


def assert_domain_covered(domain: Domain | str, *, known_uncovered: Iterable[type] = ()) -> None:
    """Check (a): the domain's handlers cover every effect it introduces."""
    resolved = get_domain(domain) if isinstance(domain, str) else domain
    _assert_domains_covered([resolved], tuple(known_uncovered))


def assert_registered_domains_covered(*, known_uncovered: Iterable[type] = ()) -> None:
    """Check (a) over every domain currently registered."""
    _assert_domains_covered(list(registered_domains()), tuple(known_uncovered))


def _assert_domains_covered(domains: list[Domain], known_uncovered: tuple[type, ...]) -> None:
    missing_by_domain: dict[str, list[type]] = {}
    for domain in domains:
        handled: set[type] = set()
        for handler in domain.handlers:
            handled |= handled_effects(handler, vocabulary=domain.effects)
        missing = [effect for effect in domain.effects if effect not in handled]
        if missing:
            missing_by_domain[domain.name] = missing

    known = set(known_uncovered)
    all_missing = {effect for missing in missing_by_domain.values() for effect in missing}
    unexpected = {
        name: [effect for effect in missing if effect not in known]
        for name, missing in missing_by_domain.items()
    }
    unexpected = {name: missing for name, missing in unexpected.items() if missing}
    if unexpected:
        lines = [
            f"  domain {name!r}: {', '.join(sorted(cls.__name__ for cls in missing))}"
            for name, missing in unexpected.items()
        ]
        raise DomainCoverageError(
            "domain coverage check (a) failed — introduced effects not covered "
            "by the union of the domain's handlers "
            "(ADR-DOE-DOMAIN-001 D7):\n" + "\n".join(lines)
        )

    stale = known - all_missing
    if stale:
        names = ", ".join(sorted(cls.__name__ for cls in stale))
        raise DomainCheckError(
            f"stale known_uncovered entries: {names} — these effects are now "
            f"covered (or not introduced by the checked domains); remove them "
            f"from the declaration (ratchet)"
        )


def assert_no_orphan_effects(
    packages: Iterable[str], *, known_orphans: Iterable[type] = ()
) -> None:
    """Check (c): no EffectBase subclass in the named packages lacks a domain.

    ``packages`` are module or package names; packages are import-walked
    recursively. A class counts as *defined* in the module whose name equals
    its ``__module__`` — re-exports are not counted. Import failures
    propagate (fail loud).
    """
    defined = _effect_classes_defined_in(packages)
    orphans: list[type] = []
    for effect in defined:
        try:
            introducing_domain(effect)
        except KeyError:
            orphans.append(effect)

    known = set(known_orphans)
    unexpected = [effect for effect in orphans if effect not in known]
    if unexpected:
        lines = [f"  {effect.__module__}.{effect.__name__}" for effect in unexpected]
        raise OrphanEffectError(
            "orphan effect check (c) failed — EffectBase subclasses not "
            "introduced by any registered domain (ADR-DOE-DOMAIN-001 D7):\n" + "\n".join(lines)
        )

    stale = known - set(orphans)
    if stale:
        names = ", ".join(sorted(cls.__name__ for cls in stale))
        raise DomainCheckError(
            f"stale known_orphans entries: {names} — these effects are now "
            f"introduced (or not defined in the scanned packages); remove them "
            f"from the declaration (ratchet)"
        )


def _effect_classes_defined_in(packages: Iterable[str]) -> list[type]:
    module_names: list[str] = []
    for package_name in packages:
        root = importlib.import_module(package_name)
        module_names.append(root.__name__)
        if hasattr(root, "__path__"):
            for info in pkgutil.walk_packages(
                root.__path__, prefix=root.__name__ + ".", onerror=_reraise_walk_error
            ):
                module_names.append(info.name)

    defined: dict[type, None] = {}
    for module_name in module_names:
        module = importlib.import_module(module_name)
        for value in sorted(vars(module).values(), key=_attribute_sort_key):
            if (
                isinstance(value, type)
                and issubclass(value, EffectBase)
                and value is not EffectBase
                and value.__module__ == module_name
            ):
                defined[value] = None
    return list(defined)


def _attribute_sort_key(value: object) -> str:
    if isinstance(value, type):
        return value.__name__
    return ""


def _reraise_walk_error(module_name: str) -> None:
    # pkgutil.walk_packages swallows ImportError by default — fail loud instead.
    error = sys.exc_info()[1]
    if error is not None:
        raise error
    raise ImportError(f"failed to import {module_name} during orphan-effect scan")
