"""The scheduler's table-size diagnostic is a public effect: ``SchedulerIntrospection``.

``_SchedulerIntrospection`` was documented "Not public API", yet doeff's own tests, doeff-records'
tests and agora-controllers' tests all import it — it is the only way to read how many rows the
scheduler's tables hold (they are closure state), and doeff-domain lists it in the
``doeff-scheduler`` vocabulary. A name that other packages must import has to be public, or every
strict type check of those tests reports ``reportPrivateUsage`` for it (agora-controllers, card
ki-f26dcea37a93).
"""

import pytest
from doeff_core_effects.scheduler import (
    SchedulerIntrospection,
    Spawn,
    Wait,
    scheduled,
)

from doeff import do
from doeff import run as doeff_run

TABLES = {"tasks", "promises", "semaphores", "waiters", "ready", "handle_refs", "handle_ref_total"}


@pytest.mark.parametrize("implementation", ["python", "rust"])
def test_the_scheduler_answers_the_public_introspection_effect_with_its_table_sizes(
    implementation: str,
) -> None:
    @do
    def child():
        return 7

    @do
    def body():
        task = yield Spawn(child())
        yield Wait(task)
        return (yield SchedulerIntrospection())

    sizes = doeff_run(scheduled(body(), implementation=implementation))

    assert set(sizes) == TABLES, sizes
    assert all(isinstance(rows, int) and rows >= 0 for rows in sizes.values()), sizes


def test_the_public_name_documents_a_diagnostic_effect_not_a_private_hook() -> None:
    doc = SchedulerIntrospection.__doc__ or ""

    assert "Not public API" not in doc, doc
    assert "diagnostic" in doc.lower(), doc
