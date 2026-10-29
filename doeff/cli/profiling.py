"""Profiling utilities for CLI operations.

Profiling is off by default. To print how long each CLI step takes on stderr:
    doeff run --profile ...
    export DOEFF_PROFILE=1
"""

import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from doeff_core_effects._handlers import env_var_ask, reader
from doeff_core_effects._scheduler import scheduled

from doeff import Ask, run

# env_var_ask reads DOEFF_<key>, so this key is the environment variable DOEFF_PROFILE.
_PROFILE_KEY = "PROFILE"
# Values of DOEFF_PROFILE that leave profiling off, like an unset DOEFF_PROFILE.
_PROFILE_OFF_VALUES = ("", "0")


@dataclass(frozen=True)
class ProfilingConfig:
    enabled: bool = False


_DEFAULT_CONFIG = ProfilingConfig()
_CURRENT_CONFIG: ContextVar[ProfilingConfig | None] = ContextVar(
    "doeff_cli_profiling_config",
    default=None,
)


def profiling_config_from_environment() -> ProfilingConfig:
    """DOEFF_PROFILE set to a value other than "" or "0" turns profiling on.

    The variable is asked through env_var_ask (Python, in doeff-core-effects' foundation layer that
    may read the process environment) and not through Hy's process effects, so ``doeff run`` starts
    in an environment that has only the four core packages and no Hy (agora-redesign #3012).
    """
    setting = run(scheduled()(reader(env={_PROFILE_KEY: ""})(env_var_ask()(Ask(_PROFILE_KEY)))))
    return ProfilingConfig(enabled=setting not in _PROFILE_OFF_VALUES)


@contextmanager
def use_profiling_config(config: ProfilingConfig) -> Generator[None, None, None]:
    token = _CURRENT_CONFIG.set(config)
    try:
        yield
    finally:
        _CURRENT_CONFIG.reset(token)


@contextmanager
def profile(
    operation: str,
    *,
    indent: int = 0,
    config: ProfilingConfig | None = None,
) -> Generator[None, None, None]:
    if not is_profiling_enabled(config):
        yield
        return

    indent_str = "  " * indent
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        print(f"[DOEFF][PROFILE] {indent_str}{operation}: {elapsed_ms:.2f}ms", file=sys.stderr)


def is_profiling_enabled(config: ProfilingConfig | None = None) -> bool:
    return (config or _CURRENT_CONFIG.get() or _DEFAULT_CONFIG).enabled


def print_profiling_status(config: ProfilingConfig | None = None) -> None:
    if is_profiling_enabled(config):
        print(
            "[DOEFF][PROFILE] Profiling enabled by --profile or DOEFF_PROFILE.",
            file=sys.stderr,
        )
