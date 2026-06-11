from __future__ import annotations

from doeff import Ask, do
from tests._run_helpers import run_with_defaults


def test_hashable_env_keys() -> None:
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

    result = run_with_defaults(
        program(),
        env={key: "postgres://localhost"},
    )
    assert result.value == "postgres://localhost"


def test_string_keys_still_work() -> None:
    """String keys continue to work after HashedPyKey migration."""

    @do
    def program():
        return (yield Ask("key"))

    result = run_with_defaults(
        program(),
        env={"key": "value"},
    )
    assert result.value == "value"
