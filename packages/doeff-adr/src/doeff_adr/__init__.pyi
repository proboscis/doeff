"""Executable ADR contracts for doeff projects."""

import doeff_hy as doeff_hy

__all__: tuple[str, ...]

from doeff_adr._effects import (
    AdrIds as AdrIds,
)
from doeff_adr._effects import (
    GetAdr as GetAdr,
)
from doeff_adr._effects import (
    adr_registry as adr_registry,
)
from doeff_adr._pytest_plugin import DEFAULT_FILE_PATTERNS as DEFAULT_FILE_PATTERNS
from doeff_adr._pytest_plugin import DoeffAdrHyFile as DoeffAdrHyFile
from doeff_adr._registry import (
    AdrSpec as AdrSpec,
)
from doeff_adr._registry import AdrStatus as AdrStatus
from doeff_adr._registry import EnforcementMode as EnforcementMode
from doeff_adr._registry import (
    EnforcementRef as EnforcementRef,
)
from doeff_adr._registry import FixtureSpec as FixtureSpec
from doeff_adr._registry import (
    SemgrepSpec as SemgrepSpec,
)
from doeff_adr._registry import (
    assert_adr_contract as assert_adr_contract,
)
from doeff_adr._registry import (
    assert_all_adr_contracts as assert_all_adr_contracts,
)
from doeff_adr._registry import (
    clear_registry as clear_registry,
)
from doeff_adr._registry import (
    enforcement_ids as enforcement_ids,
)
from doeff_adr._registry import (
    get_enforcement as get_enforcement,
)
from doeff_adr._registry import (
    register_adr as register_adr,
)
from doeff_adr._registry import (
    register_deftest_enforcement as register_deftest_enforcement,
)
from doeff_adr._semgrep_effects import (
    AssertSemgrep as AssertSemgrep,
)
from doeff_adr._semgrep_effects import (
    RegisterSemgrep as RegisterSemgrep,
)
from doeff_adr._semgrep_effects import (
    semgrep_enforcements as semgrep_enforcements,
)
from doeff_adr._semgrep_hy import HyScanExpansionError as HyScanExpansionError
