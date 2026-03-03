"""WithIntercept × arbitrary stacking of sub-program-scoping constructs.

Tests that WithIntercept's interceptor sees effects from the innermost program
regardless of where WithIntercept sits in the wrapper stack.

Sub-program-scoping constructs tested:
  - Local(env, prog)      — uses EvalInScope (LazyAskHandler)
  - Try(prog)             — uses EvalInScope (ResultSafeHandler)
  - WithHandler(h, prog)  — VM-level PromptBoundary
  - Listen(prog)          — Python wrapper over WithHandler

Stacking positions for W = WithIntercept:
  Single:  W(A(expr)),  A(W(expr))
  Pair:    W(A(B(expr))),  A(W(B(expr))),  A(B(W(expr)))
"""

from __future__ import annotations

import doeff_vm
import pytest

from doeff import (
    Ask,
    Effect,
    Listen,
    Local,
    Tell,
    Try,
    WithHandler,
    WithIntercept,
    WriterTellEffect,
    do,
)


# ---------------------------------------------------------------------------
# Programs
# ---------------------------------------------------------------------------


@do
def _tell_program(msg: str = "hello"):
    yield Tell(msg)
    return "done"


@do
def _multi_tell_program():
    yield Tell("first")
    yield Tell("second")
    yield Tell("third")
    return "done"


@do
def _tell_and_ask_program():
    val = yield Ask("k")
    yield Tell(f"got:{val}")
    return val


# ---------------------------------------------------------------------------
# Observer (interceptor that records Tell messages)
# ---------------------------------------------------------------------------


def _make_observer():
    seen: list[str] = []

    @do
    def observe(effect: Effect):
        if isinstance(effect, WriterTellEffect):
            seen.append(effect.message)
        return effect

    return observe, seen


# ---------------------------------------------------------------------------
# Wrapper factories
# ---------------------------------------------------------------------------


@do
def _pass_handler(effect: Effect, k):
    yield doeff_vm.Pass()


def _wrap_local(prog):
    return Local({"k": "v"}, prog)


def _wrap_try(prog):
    return Try(prog)


def _wrap_with_handler(prog):
    return WithHandler(_pass_handler, prog)


def _wrap_listen(prog):
    return Listen(prog)


WRAPPERS = [
    pytest.param(_wrap_local, id="Local"),
    pytest.param(_wrap_try, id="Try"),
    pytest.param(_wrap_with_handler, id="WithHandler"),
    pytest.param(_wrap_listen, id="Listen"),
]

W_POS_SINGLE = [
    pytest.param("outer", id="W_outer"),
    pytest.param("inner", id="W_inner"),
]

W_POS_PAIR = [
    pytest.param("outer", id="W_outer"),
    pytest.param("middle", id="W_middle"),
    pytest.param("inner", id="W_inner"),
]


# ---------------------------------------------------------------------------
# Standalone WithIntercept (baseline)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intercept_alone(parameterized_interpreter):
    observe, seen = _make_observer()
    wrapped = WithIntercept(observe, _tell_program(), (WriterTellEffect,), "include")
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert seen == ["hello"]


# ---------------------------------------------------------------------------
# W + 1 wrapper, both positions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("wrap", WRAPPERS)
@pytest.mark.parametrize("w_pos", W_POS_SINGLE)
async def test_single_stacking(wrap, w_pos, parameterized_interpreter):
    observe, seen = _make_observer()
    w = lambda p: WithIntercept(observe, p, (WriterTellEffect,), "include")
    expr = _tell_program()
    if w_pos == "outer":
        wrapped = w(wrap(expr))
    else:
        wrapped = wrap(w(expr))
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert seen == ["hello"]


# ---------------------------------------------------------------------------
# W + 2 wrappers, all 3 positions of W
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("a", WRAPPERS)
@pytest.mark.parametrize("b", WRAPPERS)
@pytest.mark.parametrize("w_pos", W_POS_PAIR)
async def test_pair_stacking(a, b, w_pos, parameterized_interpreter):
    observe, seen = _make_observer()
    w = lambda p: WithIntercept(observe, p, (WriterTellEffect,), "include")
    expr = _tell_program()
    if w_pos == "outer":
        wrapped = w(a(b(expr)))
    elif w_pos == "middle":
        wrapped = a(w(b(expr)))
    else:
        wrapped = a(b(w(expr)))
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert seen == ["hello"]


# ---------------------------------------------------------------------------
# Multi-tell: all effects captured, not just the first
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("wrap", WRAPPERS)
@pytest.mark.parametrize("w_pos", W_POS_SINGLE)
async def test_multi_tell(wrap, w_pos, parameterized_interpreter):
    observe, seen = _make_observer()
    w = lambda p: WithIntercept(observe, p, (WriterTellEffect,), "include")
    expr = _multi_tell_program()
    if w_pos == "outer":
        wrapped = w(wrap(expr))
    else:
        wrapped = wrap(w(expr))
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert seen == ["first", "second", "third"]


# ---------------------------------------------------------------------------
# Local + Ask: env propagation through interceptor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("w_pos", W_POS_SINGLE)
async def test_local_ask(w_pos, parameterized_interpreter):
    observe, seen = _make_observer()
    w = lambda p: WithIntercept(observe, p, (WriterTellEffect,), "include")
    expr = _tell_and_ask_program()
    if w_pos == "outer":
        wrapped = w(Local({"k": "val"}, expr))
    else:
        wrapped = Local({"k": "val"}, w(expr))
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert result.value == "val"
    assert seen == ["got:val"]


# ---------------------------------------------------------------------------
# Handler-emitted Tell through EvalInScope wrappers
# (verifies k_origin fix works across Local/Try)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "wrapper",
    [
        pytest.param(lambda p: Local({"x": "env"}, p), id="Local"),
        pytest.param(lambda p: Try(p), id="Try"),
    ],
)
async def test_handler_tell_through_eval_in_scope(wrapper, parameterized_interpreter):
    observe, seen = _make_observer()

    @do
    def telling_handler(effect: Effect, k):
        if isinstance(effect, doeff_vm.PyAsk):
            yield Tell("from-handler")
            return (yield doeff_vm.Resume(k, "handled"))
        yield doeff_vm.Pass()

    @do
    def body():
        val = yield Ask("x")
        yield Tell(f"from-program:{val}")
        return val

    wrapped = WithIntercept(
        observe,
        WithHandler(telling_handler, wrapper(body())),
        (WriterTellEffect,),
        "include",
    )
    result = await parameterized_interpreter.run_async(wrapped)
    assert result.is_ok
    assert "from-handler" in seen
    assert "from-program:handled" in seen


# ---------------------------------------------------------------------------
# Mediagen topology: exact pattern that broke mediagen's slog logging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mediagen_topology(parameterized_interpreter):
    observe, seen = _make_observer()

    @do
    def domain_handler(effect: Effect, k):
        yield doeff_vm.Pass()

    program = _tell_program("slog-message")
    intercepted = WithIntercept(
        observe, Local({"asset_registry": {}}, program), (WriterTellEffect,), "include"
    )
    with_handlers = WithHandler(domain_handler, intercepted)
    result = await parameterized_interpreter.run_async(with_handlers)
    assert result.is_ok
    assert seen == ["slog-message"]
