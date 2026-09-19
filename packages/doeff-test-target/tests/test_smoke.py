import sys
from pathlib import Path

from doeff import Maybe

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from doeff_test_target import mock_handlers, orchestrate, production_handlers
from doeff_test_target.combinators.advanced import iota, theta
from doeff_test_target.core.beta import beta
from doeff_test_target.core.gamma import gamma
from doeff_test_target.scenarios.first_choice import choose_first_some


@pytest.mark.awaiting_api_migration(
    reason="test asserts Maybe.from_optional, which the current Maybe does not expose - #619"
)
def test_package_imports_and_public_api_usage():
    assert callable(orchestrate)
    assert callable(production_handlers)
    assert callable(mock_handlers)
    assert callable(beta)
    assert callable(gamma)
    assert callable(theta)
    assert callable(iota)
    assert callable(choose_first_some)
    assert choose_first_some() is not None
    assert hasattr(Maybe, "from_optional")
