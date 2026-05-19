"""Profiling utilities for CLI operations.

Profiling is enabled by default. To disable:
    export DOEFF_DISABLE_PROFILE=1
"""

import sys
import time
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

_DISABLE_PROFILE_ENV = "DOEFF_DISABLE_PROFILE"


@dataclass(frozen=True)
class ProfilingConfig:
    enabled: bool = True


_DEFAULT_CONFIG = ProfilingConfig()
_CURRENT_CONFIG: ContextVar[ProfilingConfig | None] = ContextVar(
    "doeff_cli_profiling_config",
    default=None,
)


def profiling_config_from_env(env: Mapping[str, str]) -> ProfilingConfig:
    return ProfilingConfig(enabled=not bool(env.get(_DISABLE_PROFILE_ENV)))


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
            "[DOEFF][PROFILE] Profiling enabled. To disable: export DOEFF_DISABLE_PROFILE=1",
            file=sys.stderr,
        )
