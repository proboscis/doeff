
from datetime import datetime, timezone

import pytest
from doeff_time.effects import WaitUntil, WaitUntilEffect


def test_wait_until_factory_creates_effect() -> None:
    target = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    effect = WaitUntil(target)
    assert isinstance(effect, WaitUntilEffect)
    assert effect.target == target


def test_wait_until_rejects_naive_target() -> None:
    naive_target = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc).replace(tzinfo=None)
    with pytest.raises(ValueError, match="target must be timezone-aware datetime"):
        WaitUntil(naive_target)
