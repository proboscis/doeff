from __future__ import annotations

from typing import Any


class MissingEnvKeyError(KeyError):
    """Raised when Ask effect cannot find the requested key in the environment."""

    def __init__(self, key: Any) -> None:
        self.key = key
        super().__init__(
            f"Environment key not found: {key!r}\n"
            f"Hint: Provide this key via `env={{'{key}': value}}` or wrap with `Local({{'{key}': value}}, ...)`"
        )


__all__ = ["MissingEnvKeyError"]
