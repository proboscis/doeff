import asyncio

import pytest

from doeff import Gather, CESKInterpreter, Step, do
from doeff.types import EffectGenerator


@do
def _feature(name: str) -> EffectGenerator[str]:
    yield Step(name)
    return name


@do
def _program() -> EffectGenerator[list[str]]:
    results = yield Gather(_feature("a"), _feature("b"))
    return results


async def _run():
    interpreter = CESKInterpreter()
    result = await interpreter.run_async(_program())
    return result.graph


@pytest.mark.xfail(reason="CESK runtime does not track computation graphs (no-op handlers)")
def test_gather_creates_multiple_inputs():
    graph = asyncio.run(_run())

    last_inputs = {node.value for node in graph.last.inputs}

    assert last_inputs == {"a", "b"}
