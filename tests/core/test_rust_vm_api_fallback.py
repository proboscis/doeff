from __future__ import annotations

import pytest

from doeff import Program
from doeff.rust_vm import async_run, default_handlers, run


def test_run_pure_program_with_default_handlers() -> None:
    result = run(Program.pure(123), handlers=default_handlers())
    assert result.value == 123


@pytest.mark.asyncio
async def test_async_run_pure_program_with_default_handlers() -> None:
    result = await async_run(Program.pure(456), handlers=default_handlers())
    assert result.value == 456
