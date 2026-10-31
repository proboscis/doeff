"""The packages' handlers without settings are handler values, not factories called with no arguments
(ADR-DOE-API-002).

doeff-git, doeff-openai and doeff-openrouter's ``production_handlers`` and doeff-conductor's
``agent_handler`` and ``git_handler`` are put in a stack as they are; the former call answers the same
handler with a DeprecationWarning for one release.

former-factory-call-rewrite: keep — the tests call the former form on purpose.
"""

import warnings
from pathlib import Path

import pytest
from doeff_conductor import agent_handler, git_handler
from doeff_git import GitCommit, git_local_handler, github_handler
from doeff_git import production_handlers as git_production_handlers
from doeff_openai import production_handlers as openai_production_handlers
from doeff_openrouter import production_handlers as openrouter_production_handlers

from doeff import K, Resume, do, handler, run, with_handlers

_FORMER_FACTORIES = {
    "doeff_git.production_handlers": git_production_handlers,
    "doeff_openai.production_handlers": openai_production_handlers,
    "doeff_openrouter.production_handlers": openrouter_production_handlers,
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


@handler
def _commit_stub(effect: GitCommit, k: K):
    """Answer GitCommit without running git, so the bundle is seen passing the effect inward."""
    return (yield Resume(k, f"committed {effect.message}"))


@do
def _commits():
    """A program that commits once — what doeff-git's bundle answers."""
    return (yield GitCommit(work_dir=Path("."), message="m"))


def test_the_git_bundle_goes_in_a_stack_without_a_call():
    """``[production_handlers, stub]`` — the stack takes the bundle as it is, and it still installs
    ``github_handler`` outside ``git_local_handler`` (the effects it does not answer pass on)."""
    assert run(with_handlers([git_production_handlers, _commit_stub], _commits())) == "committed m"
    assert git_production_handlers.__doc__ == (
        "Handlers stacked as one installer, outermost first: "
        f"{github_handler.__name__}, {git_local_handler.__name__}"
    )


def test_the_former_git_call_runs_the_same_bundle():
    """``production_handlers()(prog)`` still runs while callers are rewritten."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert run(git_production_handlers()(_commit_stub(_commits()))) == "committed m"
