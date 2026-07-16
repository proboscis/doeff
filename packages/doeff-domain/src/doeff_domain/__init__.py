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

from .checks import (
    assert_domain_covered as assert_domain_covered,
)
from .checks import (
    assert_no_orphan_effects as assert_no_orphan_effects,
)
from .checks import (
    assert_registered_domains_covered as assert_registered_domains_covered,
)
from .introspect import (
    BODY_ATTRIBUTE as BODY_ATTRIBUTE,
)
from .introspect import (
    HANDLES_ATTRIBUTE as HANDLES_ATTRIBUTE,
)
from .introspect import (
    handled_effects as handled_effects,
)
from .introspect import (
    handles as handles,
)
from .registry import (
    Domain as Domain,
)
from .registry import (
    DomainCheckError as DomainCheckError,
)
from .registry import (
    DomainCoverageError as DomainCoverageError,
)
from .registry import (
    DomainDefinitionError as DomainDefinitionError,
)
from .registry import (
    DomainError as DomainError,
)
from .registry import (
    DomainLaw as DomainLaw,
)
from .registry import (
    DomainRegistrationError as DomainRegistrationError,
)
from .registry import (
    DomainTerm as DomainTerm,
)
from .registry import (
    DuplicateDomainNameError as DuplicateDomainNameError,
)
from .registry import (
    DuplicateEffectIntroductionError as DuplicateEffectIntroductionError,
)
from .registry import (
    OrphanEffectError as OrphanEffectError,
)
from .registry import (
    clear_registry as clear_registry,
)
from .registry import (
    domain_names as domain_names,
)
from .registry import (
    get_domain as get_domain,
)
from .registry import (
    introducing_domain as introducing_domain,
)
from .registry import (
    isolated_registry as isolated_registry,
)
from .registry import (
    register_domain as register_domain,
)
from .registry import (
    registered_domains as registered_domains,
)
