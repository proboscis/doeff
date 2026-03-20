from __future__ import annotations

from dataclasses import dataclass

import doeff
from doeff import (
    AcquireSemaphore,
    CreateSemaphore,
    Discontinued,
    Effect,
    Gather,
    Get,
    Modify,
    Pass,
    Pure,
    Put,
    ReleaseSemaphore,
    Resume,
    Spawn,
    Transfer,
    Try,
    WithHandler,
    default_handlers,
    do,
    run,
)
from doeff.types import EffectBase, EffectGenerator


@dataclass(frozen=True, kw_only=True)
class Ping(EffectBase):
    label: str


@do
def _resume_handler(effect: Effect, k: object) -> EffectGenerator:
    if isinstance(effect, Ping):
        return (yield Resume(k, f"handled:{effect.label}"))
    yield Pass()


@do
def _transfer_handler(effect: Effect, k: object) -> EffectGenerator:
    if isinstance(effect, Ping):
        yield Transfer(k, f"handled:{effect.label}")
    yield Pass()


@do
def _discontinue_handler(effect: Effect, k: object) -> EffectGenerator:
    if isinstance(effect, Ping):
        yield doeff.Discontinue(k)
    yield Pass()


def test_discontinue_basic() -> None:
    @do
    def program():
        try:
            _ = yield Ping(label="x")
            return "unreachable"
        except Discontinued:
            return "discontinued"

    result = run(
        WithHandler(_discontinue_handler, program()), handlers=default_handlers(), store={}
    )
    assert result.value == "discontinued"


def test_discontinue_with_custom_exception() -> None:
    @do
    def custom_handler(effect: Effect, k: object) -> EffectGenerator:
        if isinstance(effect, Ping):
            yield doeff.Discontinue(k, ValueError("reason"))
        yield Pass()

    @do
    def program():
        try:
            _ = yield Ping(label="x")
            return "unreachable"
        except ValueError as exc:
            return str(exc)

    result = run(WithHandler(custom_handler, program()), handlers=default_handlers(), store={})
    assert result.value == "reason"


def test_discontinue_try_finally_cleanup() -> None:
    @do
    def program():
        yield Put("trace", [])
        try:
            yield Modify("trace", lambda xs: [*xs, "body"])
            _ = yield Ping(label="x")
            return "unreachable"
        finally:
            yield Modify("trace", lambda xs: [*xs, "cleanup"])

    @do
    def wrapper():
        outcome = yield Try(WithHandler(_discontinue_handler, program()))
        trace = yield Get("trace")
        return outcome, trace

    result = run(wrapper(), handlers=default_handlers(), store={})
    outcome, trace = result.value
    assert outcome.is_err()
    assert isinstance(outcome.error, Discontinued)
    assert trace == ["body", "cleanup"]


def test_handler_abandon_raises_error() -> None:
    @do
    def abandon_handler(effect: Effect, k: object) -> EffectGenerator:
        if isinstance(effect, Ping):
            return "abandoned"
        yield Pass()

    @do
    def program():
        _ = yield Ping(label="x")
        return "unreachable"

    result = run(WithHandler(abandon_handler, program()), handlers=default_handlers(), store={})
    assert result.is_err()
    assert isinstance(result.error, RuntimeError)
    assert "handler returned without consuming continuation" in str(result.error)


def test_handler_returning_doexpr_without_consuming_k_raises_error() -> None:
    @do
    def abandon_with_doexpr_handler(effect: Effect, k: object) -> EffectGenerator:
        if isinstance(effect, Ping):
            return Pure("abandoned")
        yield Pass()

    @do
    def program():
        _ = yield Ping(label="x")
        return "unreachable"

    result = run(
        WithHandler(abandon_with_doexpr_handler, program()),
        handlers=default_handlers(),
        store={},
    )
    assert result.is_err()
    assert isinstance(result.error, RuntimeError)
    assert "handler returned without consuming continuation" in str(result.error)


def test_try_finally_with_semaphore_no_finally_doctrl() -> None:
    @do
    def worker(sem, value: int):
        yield AcquireSemaphore(sem)
        try:
            return value * 10
        finally:
            yield ReleaseSemaphore(sem)

    @do
    def program():
        sem = yield CreateSemaphore(2)
        tasks = []
        for i in range(6):
            tasks.append((yield Spawn(worker(sem, i), daemon=False)))
        return list((yield Gather(*tasks)))

    result = run(program(), handlers=default_handlers(), store={})
    assert result.value == [i * 10 for i in range(6)]


def test_try_finally_with_resume() -> None:
    @do
    def program():
        yield Put("cleaned", False)
        try:
            value = yield Ping(label="x")
            return value
        finally:
            yield Put("cleaned", True)

    @do
    def wrapper():
        value = yield WithHandler(_resume_handler, program())
        cleaned = yield Get("cleaned")
        return value, cleaned

    result = run(wrapper(), handlers=default_handlers(), store={})
    assert result.value == ("handled:x", True)


def test_try_finally_with_transfer() -> None:
    @do
    def program():
        yield Put("cleaned", False)
        try:
            value = yield Ping(label="x")
            return value
        finally:
            yield Put("cleaned", True)

    @do
    def wrapper():
        value = yield WithHandler(_transfer_handler, program())
        cleaned = yield Get("cleaned")
        return value, cleaned

    result = run(wrapper(), handlers=default_handlers(), store={})
    assert result.value == ("handled:x", True)
