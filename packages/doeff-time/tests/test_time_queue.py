from datetime import datetime, timedelta, timezone

import pytest
from doeff_time._internals import TimeQueue

from doeff.effects.spawn import Promise


def _promise(promise_id: int) -> Promise[None]:
    return Promise(_promise_handle={"type": "Promise", "promise_id": promise_id})


def test_time_queue_orders_entries_by_datetime() -> None:
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    queue = TimeQueue()

    first = _promise(1)
    second = _promise(2)
    third = _promise(3)

    queue.push(base + timedelta(seconds=3), third)
    queue.push(base + timedelta(seconds=1), first)
    queue.push(base + timedelta(seconds=2), second)

    entry1 = queue.pop()
    entry2 = queue.pop()
    entry3 = queue.pop()

    assert entry1.time == base + timedelta(seconds=1)
    assert entry2.time == base + timedelta(seconds=2)
    assert entry3.time == base + timedelta(seconds=3)
    assert entry1.promise is first
    assert entry2.promise is second
    assert entry3.promise is third


def test_time_queue_rejects_naive_datetime() -> None:
    queue = TimeQueue()
    naive = datetime(2024, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)
    with pytest.raises(ValueError, match="time must be timezone-aware datetime"):
        queue.push(naive, _promise(1))
