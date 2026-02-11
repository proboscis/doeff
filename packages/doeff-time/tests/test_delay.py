from __future__ import annotations

import pytest
from doeff_time.effects import Delay, DelayEffect


def test_delay_factory_creates_effect() -> None:
    effect = Delay(1.5)
    assert isinstance(effect, DelayEffect)
    assert effect.seconds == 1.5


@pytest.mark.parametrize("value", [-1, -0.001])
def test_delay_rejects_negative_seconds(value: float) -> None:
    with pytest.raises(ValueError, match=r"seconds must be >= 0\.0"):
        Delay(value)
