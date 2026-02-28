
import pytest
from doeff_time.effects import WaitUntil, WaitUntilEffect


def test_wait_until_factory_creates_effect() -> None:
    effect = WaitUntil(1_704_067_200.0)
    assert isinstance(effect, WaitUntilEffect)
    assert effect.target == 1_704_067_200.0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_wait_until_rejects_non_finite_target(value: float) -> None:
    with pytest.raises(ValueError, match="target must be finite"):
        WaitUntil(value)
