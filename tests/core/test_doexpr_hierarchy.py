from __future__ import annotations

import pytest
from dataclasses import dataclass

from doeff import Ask, Program, ProgramBase, do
from doeff import EffectBase
from tests._run_helpers import run_with_defaults



def test_effects_do_not_expose_direct_composition_methods() -> None:
    effect = Ask("token")

    assert not hasattr(effect, "map")
    assert not hasattr(effect, "flat_map")



@dataclass(frozen=True)
class _LocalEffect(EffectBase):
    value: int
