"""doeff-conductor's handlers without settings are handler values, not factories called with no
arguments (ADR-DOE-API-002).

``agent_handler`` and ``git_handler`` are put in a stack as they are; the former call answers the
same handler with a DeprecationWarning for one release. Split from the root
tests/test_former_factory_handlers_of_the_packages.py, which checks the other packages.

former-factory-call-rewrite: keep — the tests call the former form on purpose.
"""

import pytest
from doeff_conductor import agent_handler, git_handler

_FORMER_FACTORIES = {
    "doeff_conductor.agent_handler": agent_handler,
    "doeff_conductor.git_handler": git_handler,
}


@pytest.mark.parametrize("value", _FORMER_FACTORIES.values(), ids=_FORMER_FACTORIES.keys())
def test_it_is_a_marked_handler_value(value):
    """It carries the handler marker without being called, so it goes in a stack as it is."""
    assert vars(value)["_doeff_is_handler_fn"] is True


@pytest.mark.parametrize("value", _FORMER_FACTORIES.values(), ids=_FORMER_FACTORIES.keys())
def test_the_former_call_warns_and_answers_the_same_handler(value):
    """``name()`` keeps running for one release: a DeprecationWarning, and the handler itself."""
    with pytest.warns(DeprecationWarning, match=f"{value.__name__} has no settings"):
        called = value()
    assert called is value
