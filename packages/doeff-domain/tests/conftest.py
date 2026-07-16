import importlib
import sys
from pathlib import Path

import hy  # noqa: F401 - installs the Hy import hook for test fixtures
import pytest


TESTS_DIRECTORY = Path(__file__).parent


@pytest.fixture(autouse=True)
def isolated_domain_registry():
    from doeff_domain.registry import clear_registry

    clear_registry()
    sys.path.insert(0, str(TESTS_DIRECTORY))
    for module_name in (
        "doeff_domain.dogfood",
        "doeff_domain_macro_fixture",
        "doeff_domain_test_handlers",
    ):
        sys.modules.pop(module_name, None)
    importlib.invalidate_caches()
    yield
    clear_registry()
    sys.path.remove(str(TESTS_DIRECTORY))
    for module_name in (
        "doeff_domain.dogfood",
        "doeff_domain_macro_fixture",
        "doeff_domain_test_handlers",
    ):
        sys.modules.pop(module_name, None)
    importlib.invalidate_caches()
