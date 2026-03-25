
from datetime import datetime, timezone

from doeff_time.effects import GetTime
from doeff_time.handlers import sync_time_handler

from conftest import run_with_handlers
from doeff import WithHandler, do


@do
def _read_time_program():
    return (yield GetTime())


def test_get_time_sync_handler_returns_current_time() -> None:
    before = datetime.now(timezone.utc)
    result = run_with_handlers(
        WithHandler(sync_time_handler(), _read_time_program()),
    )
    after = datetime.now(timezone.utc)

    assert isinstance(result, datetime)
    assert before <= result <= after
