"""The effect analyzer's places and switches as the environment names them — the one module that reads it.

The analyzer runs as a tool (CLI, editor, pytest collection) outside any doeff Program, so its cache
place comes from environment variables, not from an Ask. This module only reads them: each function
returns the raw value of one variable (or the XDG cache base). What a value means — "off" turns a
cache off, which place wins — stays with the callers. ``packages/doeff-effect-analyzer/architecture.hy``
names this module and takes it out of DOEFF004 (agora-redesign #2860); other modules of the package
still may not read the environment.
"""

import os
from pathlib import Path


def cache_home() -> Path:
    """The XDG cache base ($XDG_CACHE_HOME, else ~/.cache)."""
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")


def tree_cache_setting() -> str | None:
    """The raw $DOEFF_EFFECT_ANALYZER_CACHE (a directory, or "off"), or None when unset."""
    return os.environ.get("DOEFF_EFFECT_ANALYZER_CACHE")


def result_cache_setting() -> str | None:
    """The raw $DOEFF_EFFECT_ANALYZER_RESULT_CACHE ("off" turns the result cache off), or None."""
    return os.environ.get("DOEFF_EFFECT_ANALYZER_RESULT_CACHE")
