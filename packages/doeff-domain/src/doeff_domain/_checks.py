"""Opt-in conformance checks (ADR-DOE-DOMAIN-001 D7) — the bodies the domain table handler answers with.

Nothing here runs by default — adopting projects perform the check effects
(AssertDomainCovered・AssertRegisteredDomainsCovered・AssertNoOrphanEffects)
in their own tests under ``domain_registry``. doeff itself adopts them for its
own packages (dogfood).

- ``assert_domain_covered`` — check (a): the union of the domain handlers'
  handled sets must be a superset of the effects the domain *introduces*
  (included vocabulary is the introducing domain's responsibility).
- ``assert_no_orphan_effects`` — check (c): every ``EffectBase`` subclass
  defined at module level in the named packages must be introduced by some
  domain of the table. Double introduction is already impossible at
  registration time (D3).

Both checks accept an explicit known-gap parameter with ratchet semantics:
a declared gap that is no longer real fails as stale, forcing its removal.
"""

import importlib
import pkgutil
import sys
from collections.abc import Iterable
from types import ModuleType

from doeff import EffectBase
from doeff_domain._introspect import handled_effects
from doeff_domain._registry import (
    Domain,
    DomainCheckError,
    DomainCoverageError,
    DomainTable,
    OrphanEffectError,
)


def assert_domain_covered(
    table: DomainTable, domain: Domain | str, *, known_uncovered: Iterable[type] = ()
) -> None:
    """Check (a): the domain's handlers cover every effect it introduces (a name is read from the table)."""
    resolved = table.get(domain) if isinstance(domain, str) else domain
    _assert_domains_covered([resolved], tuple(known_uncovered))


def assert_registered_domains_covered(
    table: DomainTable, *, known_uncovered: Iterable[type] = ()
) -> None:
    """Check (a) over every domain of the table."""
    _assert_domains_covered(table.domains(), tuple(known_uncovered))


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
    table: DomainTable, packages: Iterable[str], *, known_orphans: Iterable[type] = ()
) -> None:
    """Check (c): no EffectBase subclass in the named packages lacks a domain of the table.

    ``packages`` are module or package names; packages are import-walked
    recursively. A class counts as *defined* in the module whose name equals
    its ``__module__`` — re-exports are not counted. A module that cannot be
    imported fails the check loud as a DomainCheckError (its cause is the
    import error).
    """
    defined = _effect_classes_defined_in(packages)
    orphans = [effect for effect in defined if not table.introduces(effect)]

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
    """The EffectBase subclasses defined (not re-exported) in the named modules and below — what check (c) scans."""
    module_names = [name for package_name in packages for name in _modules_under(package_name)]
    defined = {
        value: None
        for module_name in module_names
        for value in sorted(vars(_import_for_scan(module_name)).values(), key=_attribute_sort_key)
        if isinstance(value, type)
        and issubclass(value, EffectBase)
        and value is not EffectBase
        and value.__module__ == module_name
    }
    return list(defined)


def _modules_under(package_name: str) -> list[str]:
    """The named module and, for a package, every module below it (walked by import)."""
    root = _import_for_scan(package_name)
    if not hasattr(root, "__path__"):
        return [root.__name__]
    walked = pkgutil.walk_packages(
        root.__path__, prefix=root.__name__ + ".", onerror=_reraise_walk_error
    )
    return [root.__name__, *(info.name for info in walked)]


def _import_for_scan(module_name: str) -> ModuleType:
    """Import a module the scan reads. Any failure becomes a DomainCheckError naming the module, so check (c)
    fails with one error type (ADR-DOE-API-001 R24) — the original error stays as its cause."""
    try:
        return importlib.import_module(module_name)
    except Exception as error:
        raise DomainCheckError(
            f"cannot import {module_name} during the orphan-effect scan (check (c)): {error!r}"
        ) from error


def _attribute_sort_key(value: object) -> str:
    """Sort the module's values by class name so the scan reports in a stable order."""
    if isinstance(value, type):
        return value.__name__
    return ""


def _reraise_walk_error(module_name: str) -> None:
    """pkgutil.walk_packages swallows the import errors of the packages it walks by default — fail loud instead."""
    error = sys.exc_info()[1]
    raise DomainCheckError(
        f"cannot import {module_name} during the orphan-effect scan (check (c)): {error!r}"
    ) from error
