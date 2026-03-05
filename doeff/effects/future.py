"""Future/async effects."""


from collections.abc import Awaitable
from typing import Any

import doeff_vm

from doeff.handlers.await_handlers import (
    async_await_handler,
    python_async_syntax_escape_handler,
    sync_await_handler,
)

from ._validators import ensure_awaitable
from .base import Effect

PythonAsyncioAwaitEffect = doeff_vm.PythonAsyncioAwaitEffect


def await_(awaitable: Awaitable[Any]) -> PythonAsyncioAwaitEffect:
    ensure_awaitable(awaitable, name="awaitable")
    return PythonAsyncioAwaitEffect(awaitable=awaitable)


def Await(awaitable: Awaitable[Any]) -> Effect:
    ensure_awaitable(awaitable, name="awaitable")
    return PythonAsyncioAwaitEffect(awaitable=awaitable)


__all__ = [
    "Await",
    "PythonAsyncioAwaitEffect",
    "async_await_handler",
    "await_",
    "python_async_syntax_escape_handler",
    "sync_await_handler",
]
