from typing import Any

try:
    from doeff_vm import Discontinued
except ImportError:  # pragma: no cover - docs/type tooling without native module

    class Discontinued(Exception):
        """Raised when a continuation is discontinued without a custom exception."""


class MissingEnvKeyError(KeyError):
    """Raised when Ask effect cannot find the requested key in the environment."""

    def __init__(self, key: Any) -> None:
        self.key = key
        super().__init__(
            f"Environment key not found: {key!r}\n"
            f"Hint: Provide this key via `env={{'{key}': value}}` or wrap with `Local({{'{key}': value}}, ...)`"
        )

__all__ = ["Discontinued", "MissingEnvKeyError"]
