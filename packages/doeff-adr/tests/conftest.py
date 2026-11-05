"""Shared foundation for the doeff-adr tests."""

from collections.abc import Iterator

import pytest
from doeff_adr._env_places import SEMGREP_VERDICTS_ENV


@pytest.fixture(autouse=True)
def _pytester_samples_leave_no_adr_behind(request: pytest.FixtureRequest) -> Iterator[None]:
    """So that sample ADRs which pytester loads in the same process do not remain in the registry after the test.

    By default pytester.runpytest imports the sample .hy in the same process, and the defadr expansion puts it into the process registry. The loaded sample
    stays after the test, so if a sample in another file uses the same id (e.g. ADR-SAMPLE-097), whichever collection runs later fails with a duplicate ADR id
    (a reverse-dependency check after merge: in doeff 923a655d2387, test_semgrep_effects went red after test_adr_ids_effect). Tests that use pytester are run
    with an empty registry, and on exit the registry from before entry is restored (isolated_registry).
    """
    if "pytester" not in request.fixturenames:
        yield
        return
    from doeff_adr._registry import isolated_registry

    with isolated_registry():
        yield


@pytest.fixture(autouse=True)
def _no_outer_semgrep_verdict_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """So that the place where defsemgrep verdicts named by an outer run are kept is kept out of the doeff-adr mechanism tests.

    The daily run passes DOEFF_ADR_SEMGREP_VERDICTS to its children. If the mechanism tests of the packages stage read verdicts written by the root stage, tests that count how many times semgrep is
    launched, or that check the semgrep verdict itself, pass or fail without actually running semgrep (daily 10-11 16:00 for doeff —
    test_semgrep_installed_batch went red with 0 runs). Tests that check the location re-inject their own location by name.
    """
    monkeypatch.delenv(SEMGREP_VERDICTS_ENV, raising=False)
