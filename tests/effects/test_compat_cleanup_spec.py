from __future__ import annotations

import pytest

from doeff import Program
from doeff.effects.spawn import Spawn, SpawnEffect, spawn


def test_futureawaiteffect_alias_removed() -> None:
    import doeff.effects.future as future

    assert not hasattr(future, "FutureAwaitEffect")


def test_gather_futures_alias_removed() -> None:
    from doeff.effects.gather import GatherEffect

    effect = GatherEffect(items=(1, 2))
    with pytest.raises(AttributeError):
        _ = getattr(effect, "futures")


def test_spawn_deprecated_methods_removed() -> None:
    from doeff.effects.spawn import Promise, Task

    assert not hasattr(Promise, "complete")
    assert not hasattr(Promise, "fail")
    assert not hasattr(Task, "join")


def test_spawn_apis_return_spawn_effect_directly() -> None:
    child = Program.pure(1)

    lower = spawn(child)
    upper = Spawn(child)

    assert isinstance(lower, SpawnEffect)
    assert isinstance(upper, SpawnEffect)
