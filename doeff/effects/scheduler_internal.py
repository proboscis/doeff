"""Scheduler-internal effects for the Rust-backed scheduler runtime.

This module re-exports the VM-defined ``_SchedulerTaskCompleted`` effect and
defines ``WaitForExternalCompletion`` plus handlers used when doeff tasks are
idle but external async completions are still pending.
"""


import asyncio
from dataclasses import dataclass
from typing import Any

import doeff_vm

from doeff._types_internal import EffectBase

_SchedulerTaskCompleted = doeff_vm._SchedulerTaskCompleted


@dataclass(frozen=True, kw_only=True)
class WaitForExternalCompletion(EffectBase):
    """Request blocking wait for external completion queue.

    Yielded by task_scheduler_handler when:
    - Task queue is empty (no runnable doeff tasks)
    - External promises are pending (asyncio tasks running)

    Handled by:
    - sync_external_wait_handler: blocking queue.get()
    - async_external_wait_handler: PythonAsyncSyntaxEscape with run_in_executor

    See SPEC-SCHED-001 for architecture.

    Attributes:
        queue: The external completion queue (queue.Queue)
    """

    queue: Any  # queue.Queue - can't import due to circular deps


def sync_external_wait_handler(effect: Any, k: Any):
    """Handle WaitForExternalCompletion with blocking queue.get()."""
    if isinstance(effect, WaitForExternalCompletion):
        effect.queue.get(block=True)
        return (yield doeff_vm.Resume(k, None))

    yield doeff_vm.Pass()


def async_external_wait_handler(effect: Any, k: Any):
    """Handle WaitForExternalCompletion with PythonAsyncSyntaxEscape.

    Uses run_in_executor so queue waiting does not block the active event loop.
    """
    if isinstance(effect, WaitForExternalCompletion):

        async def _wait_one() -> None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, effect.queue.get, True)

        _ = yield doeff_vm.PythonAsyncSyntaxEscape(action=_wait_one)
        return (yield doeff_vm.Resume(k, None))

    yield doeff_vm.Pass()


__all__ = [
    "_SchedulerTaskCompleted",
    "async_external_wait_handler",
    "sync_external_wait_handler",
    "WaitForExternalCompletion",
]
