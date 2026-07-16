"""Opt-in effect vocabulary declarations and conformance checks."""

import hy as _hy  # noqa: F401 - enables imports of macros.hy and dogfood.hy

from doeff_domain.checks import (
    assert_domain_covered as assert_domain_covered,
)
from doeff_domain.checks import (
    assert_no_orphan_effects as assert_no_orphan_effects,
)
from doeff_domain.handlers import handled_effects as handled_effects
from doeff_domain.handlers import handles as handles
from doeff_domain.registry import (
    Domain as Domain,
)
from doeff_domain.registry import (
    DomainLaw as DomainLaw,
)
from doeff_domain.registry import (
    DomainTerm as DomainTerm,
)
from doeff_domain.registry import (
    clear_registry as clear_registry,
)
from doeff_domain.registry import (
    domain_for_effect as domain_for_effect,
)
from doeff_domain.registry import (
    get_domain as get_domain,
)
from doeff_domain.registry import (
    register_domain as register_domain,
)
from doeff_domain.registry import (
    registered_domains as registered_domains,
)
