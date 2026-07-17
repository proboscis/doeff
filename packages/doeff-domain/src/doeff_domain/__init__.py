"""doeff-domain — vocabulary cohesion domains for doeff (ADR-DOE-DOMAIN-001 E1).

Public surface:

- ``Domain`` / ``DomainTerm`` / ``DomainLaw`` — the cohesion unit as pure data.
- process registry: ``register_domain``, ``get_domain``, ``registered_domains``,
  ``domain_names``, ``introducing_domain``, ``isolated_registry``,
  ``clear_registry``.
- ``handles`` / ``handled_effects`` — opt-in annotation and the two-layer
  handled-effects derivation.
- checks (a)/(c): ``assert_domain_covered``,
  ``assert_registered_domains_covered``, ``assert_no_orphan_effects``.
- ``defdomain`` — Hy macro in ``doeff_domain.macros``
  (``(require doeff-domain.macros [defdomain])``).
"""

import hy as _hy  # noqa: F401 — registers the Hy import hook for doeff_domain.macros

from doeff_domain.checks import (
    assert_domain_covered as assert_domain_covered,
)
from doeff_domain.checks import (
    assert_no_orphan_effects as assert_no_orphan_effects,
)
from doeff_domain.checks import (
    assert_registered_domains_covered as assert_registered_domains_covered,
)
from doeff_domain.introspect import (
    BODY_ATTRIBUTE as BODY_ATTRIBUTE,
)
from doeff_domain.introspect import (
    HANDLES_ATTRIBUTE as HANDLES_ATTRIBUTE,
)
from doeff_domain.introspect import (
    handled_effects as handled_effects,
)
from doeff_domain.introspect import (
    handles as handles,
)
from doeff_domain.registry import (
    Domain as Domain,
)
from doeff_domain.registry import (
    DomainCheckError as DomainCheckError,
)
from doeff_domain.registry import (
    DomainCoverageError as DomainCoverageError,
)
from doeff_domain.registry import (
    DomainDefinitionError as DomainDefinitionError,
)
from doeff_domain.registry import (
    DomainError as DomainError,
)
from doeff_domain.registry import (
    DomainLaw as DomainLaw,
)
from doeff_domain.registry import (
    DomainRegistrationError as DomainRegistrationError,
)
from doeff_domain.registry import (
    DomainTerm as DomainTerm,
)
from doeff_domain.registry import (
    DuplicateDomainNameError as DuplicateDomainNameError,
)
from doeff_domain.registry import (
    DuplicateEffectIntroductionError as DuplicateEffectIntroductionError,
)
from doeff_domain.registry import (
    OrphanEffectError as OrphanEffectError,
)
from doeff_domain.registry import (
    clear_registry as clear_registry,
)
from doeff_domain.registry import (
    domain_names as domain_names,
)
from doeff_domain.registry import (
    get_domain as get_domain,
)
from doeff_domain.registry import (
    introducing_domain as introducing_domain,
)
from doeff_domain.registry import (
    isolated_registry as isolated_registry,
)
from doeff_domain.registry import (
    register_domain as register_domain,
)
from doeff_domain.registry import (
    registered_domains as registered_domains,
)
