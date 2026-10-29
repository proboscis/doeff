"""doeff-domain — vocabulary cohesion domains for doeff (ADR-DOE-DOMAIN-001 E1).

Public surface (ADR-DOE-API-001 R23 — card ki-03a7f0af2b64):

- ``Domain`` / ``DomainTerm`` / ``DomainLaw`` / ``Handles`` — the cohesion unit
  and its handler declarations as pure data.
- effects on the domain table: ``RegisterDomain``, ``GetDomain``,
  ``HandledEffects`` and checks (a)/(c): ``AssertDomainCovered``,
  ``AssertRegisteredDomainsCovered``, ``AssertNoOrphanEffects``.
- ``domain_registry`` — the factory of the handler that answers them from the
  process table (``domain_registry()``) or from a table of its own
  (``domain_registry(domains=[…])``).
- the errors the declarations and the checks raise (``DomainError`` and its
  children).
- ``defdomain`` — Hy macro in ``doeff_domain.macros``
  (``(require doeff-domain.macros [defdomain])``).
"""

import hy as _hy  # noqa: F401 — registers the Hy import hook for doeff_domain.macros

from doeff_domain._effects import (
    AssertDomainCovered as AssertDomainCovered,
)
from doeff_domain._effects import (
    AssertNoOrphanEffects as AssertNoOrphanEffects,
)
from doeff_domain._effects import (
    AssertRegisteredDomainsCovered as AssertRegisteredDomainsCovered,
)
from doeff_domain._effects import (
    GetDomain as GetDomain,
)
from doeff_domain._effects import (
    HandledEffects as HandledEffects,
)
from doeff_domain._effects import (
    RegisterDomain as RegisterDomain,
)
from doeff_domain._effects import (
    domain_registry as domain_registry,
)
from doeff_domain._introspect import (
    Handles as Handles,
)
from doeff_domain._registry import (
    Domain as Domain,
)
from doeff_domain._registry import (
    DomainCheckError as DomainCheckError,
)
from doeff_domain._registry import (
    DomainCoverageError as DomainCoverageError,
)
from doeff_domain._registry import (
    DomainDefinitionError as DomainDefinitionError,
)
from doeff_domain._registry import (
    DomainError as DomainError,
)
from doeff_domain._registry import (
    DomainLaw as DomainLaw,
)
from doeff_domain._registry import (
    DomainRegistrationError as DomainRegistrationError,
)
from doeff_domain._registry import (
    DomainTerm as DomainTerm,
)
from doeff_domain._registry import (
    DuplicateDomainNameError as DuplicateDomainNameError,
)
from doeff_domain._registry import (
    DuplicateEffectIntroductionError as DuplicateEffectIntroductionError,
)
from doeff_domain._registry import (
    OrphanEffectError as OrphanEffectError,
)
