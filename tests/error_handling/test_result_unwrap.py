import pytest

from doeff import EffectGenerator, CESKInterpreter, do
from doeff._vendor import Err, Ok
from doeff.effects import Fail, Safe, Unwrap


@do

def program_ok() -> EffectGenerator[int]:
    result = yield Safe(value_program())
    assert isinstance(result, Ok)
    return (yield Unwrap(result))


@do

def program_err() -> EffectGenerator[int]:
    result = yield Safe(error_program())
    assert isinstance(result, Err)
    yield Unwrap(result)


@do
def value_program() -> EffectGenerator[int]:
    return 10


@do

def error_program() -> EffectGenerator[int]:
    yield Fail(ValueError("boom"))


@pytest.mark.asyncio
@pytest.mark.xfail(reason="ResultUnwrapEffect not supported in CESK runtime")
async def test_unwrap_ok():
    engine = CESKInterpreter()
    run_result = await engine.run_async(program_ok())

    assert run_result.is_ok
    assert run_result.value == 10


@pytest.mark.asyncio
@pytest.mark.xfail(reason="ResultUnwrapEffect not supported in CESK runtime")
async def test_unwrap_err():
    engine = CESKInterpreter()
    run_result = await engine.run_async(program_err())

    assert run_result.is_err
    error = run_result.result.error
    from doeff.types import EffectFailure

    assert isinstance(error, EffectFailure)
    inner = error.cause
    while isinstance(inner, EffectFailure):
        inner = inner.cause

    assert isinstance(inner, ValueError)
    assert str(inner) == "boom"
