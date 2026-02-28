"""Public effect metadata for doeff-preset.

This package is primarily a preset handler bundle. It intentionally reuses
core doeff effects (for example ``AskEffect`` and ``WriterTellEffect``)
instead of defining new domain-specific effect classes.
"""


from .config import PRESET_CONFIG_EFFECT, PRESET_CONFIG_KEY_PREFIX, is_preset_config_key
from .log import PRESET_LOG_EFFECT, is_structured_log_message

__all__ = [
    "PRESET_CONFIG_EFFECT",
    "PRESET_CONFIG_KEY_PREFIX",
    "PRESET_LOG_EFFECT",
    "is_preset_config_key",
    "is_structured_log_message",
]
