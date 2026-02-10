"""Test compatibility utilities for doeff-openai."""

from __future__ import annotations

from typing import Any

import doeff
from doeff import async_run, default_handlers


class CompatRunResult:
    """Thin wrapper that keeps legacy helper methods used in tests."""

    def __init__(self, inner: Any):
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def format(self) -> str:
        status = "ok" if self._inner.is_ok() else "err"
        value = self._inner.value if self._inner.is_ok() else self._inner.error
        return f"RunResult(status={status}, value={value!r})"


class AsyncRuntime:
    """Compatibility shim for legacy tests using AsyncRuntime().run(...)."""

    async def run(
        self,
        program: Any,
        env: dict[Any, Any] | None = None,
        state: dict[str, Any] | None = None,
    ) -> Any:
        result = await async_run(
            program,
            handlers=default_handlers(),
            env=env,
            store=state,
        )
        return CompatRunResult(result)


# Ensure `from doeff import AsyncRuntime` works in package tests.
doeff.AsyncRuntime = AsyncRuntime
