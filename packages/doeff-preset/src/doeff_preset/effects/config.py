"""Effect metadata for preset configuration access.

`doeff-preset` resolves configuration through the core `AskEffect` using
`preset.*` keys.
"""

from __future__ import annotations

from doeff import AskEffect

PRESET_CONFIG_KEY_PREFIX = "preset."
PRESET_CONFIG_EFFECT = AskEffect


def is_preset_config_key(key: object) -> bool:
    """Return ``True`` when the key belongs to the preset config namespace."""
    return isinstance(key, str) and key.startswith(PRESET_CONFIG_KEY_PREFIX)


__all__ = [
    "PRESET_CONFIG_EFFECT",
    "PRESET_CONFIG_KEY_PREFIX",
    "is_preset_config_key",
]
