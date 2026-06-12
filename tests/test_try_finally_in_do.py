from __future__ import annotations

from dataclasses import dataclass

import doeff
from doeff import (
    AcquireSemaphore,
    CreateSemaphore,
    Effect,
    EffectBase,
    EffectGenerator,
    Gather,
    Get,
    Pass,
    Put,
    ReleaseSemaphore,
    Resume,
    Spawn,
    Transfer,
    do,
)
from doeff import handler as _install_raw_handler
from tests._run_helpers import run_with_defaults


@dataclass(frozen=True, kw_only=True)
class Ping(EffectBase):
    label: str


@do
def _resume_handler(effect: Effect, k: object) -> EffectGenerator:
    if isinstance(effect, Ping):
        return (yield Resume(k, f"handled:{effect.label}"))
    yield Pass(effect, k)


@do
def _transfer_handler(effect: Effect, k: object) -> EffectGenerator:
    if isinstance(effect, Ping):
        yield Transfer(k, f"handled:{effect.label}")
    yield Pass(effect, k)


@do
def _discontinue_handler(effect: Effect, k: object) -> EffectGenerator:
    if isinstance(effect, Ping):
        yield doeff.Discontinue(k)
    yield Pass(effect, k)







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
            tasks.append((yield Spawn(worker(sem, i))))
        return list((yield Gather(*tasks)))

    result = run_with_defaults(program(), store={})
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
        value = yield _install_raw_handler(_resume_handler)(program())
        cleaned = yield Get("cleaned")
        return value, cleaned

    result = run_with_defaults(wrapper(), store={})
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
        value = yield _install_raw_handler(_transfer_handler)(program())
        cleaned = yield Get("cleaned")
        return value, cleaned

    result = run_with_defaults(wrapper(), store={})
    assert result.value == ("handled:x", True)


def test_tail_looking_resume_with_handler_finally_keeps_resume_semantics() -> None:
    @do(non_tail=True)
    def handler(effect: Effect, k: object) -> EffectGenerator:
        if isinstance(effect, Ping):
            try:
                return (yield Resume(k, f"handled:{effect.label}"))
            finally:
                yield Put("handler_cleaned", True)
        yield Pass(effect, k)

    @do
    def program():
        return (yield Ping(label="x"))

    @do
    def wrapper():
        yield Put("handler_cleaned", False)
        value = yield _install_raw_handler(handler)(program())
        cleaned = yield Get("handler_cleaned")
        return value, cleaned

    result = run_with_defaults(wrapper(), store={})
    assert result.value == ("handled:x", True)
