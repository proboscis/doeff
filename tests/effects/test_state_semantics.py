"""Legacy CESK-era state semantics placeholder.

State behavior checks were migrated into active rust_vm suites:
- tests/core/test_sa008_runtime_contracts.py
- tests/effects/test_effect_combinations.py
Tracked by ISSUE-SPEC-009.
"""

import pytest

pytestmark = pytest.mark.skip(
    reason=(
        "Legacy CESK-era state semantics are not in the active rust_vm matrix; "
        "tracked by ISSUE-SPEC-009 migration/drop plan."
    )
)
