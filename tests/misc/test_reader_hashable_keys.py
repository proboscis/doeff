from __future__ import annotations

import pytest

from doeff import Ask, async_run, default_async_handlers, do
from doeff.effects.reader import ask


@pytest.mark.asyncio
async def test_hashable_env_keys() -> None:
    """Non-string hashable keys work as env keys."""

    class ConfigKey:
        def __init__(self, name: str) -> None:
            self.name = name

        def __hash__(self) -> int:
            return hash(self.name)

        def __eq__(self, other: object) -> bool:
            return isinstance(other, ConfigKey) and self.name == other.name

    key = ConfigKey("db_url")

    @do
    def program():
        return (yield Ask(key))

    result = await async_run(
        program(),
        handlers=default_async_handlers(),
        env={key: "postgres://localhost"},
    )
    assert result.value == "postgres://localhost"


@pytest.mark.asyncio
async def test_string_keys_still_work() -> None:
    """String keys continue to work after HashedPyKey migration."""

    @do
    def program():
        return (yield Ask("key"))

    result = await async_run(
        program(),
        handlers=default_async_handlers(),
        env={"key": "value"},
    )
    assert result.value == "value"


def test_pylocal_effect_constructible() -> None:
    """PyLocal effect can be constructed from Python."""
    from doeff_vm import PyLocal

    effect = PyLocal(env_update={"a": 1}, sub_program=None)
    assert effect.env_update == {"a": 1}


def test_ask_rejects_unhashable_keys() -> None:
    with pytest.raises(TypeError, match=r"key must be hashable"):
        ask([])  # type: ignore[arg-type]
