"""doeff-core-effects の handler の effect の引数の型の註の検(agora-redesign #1954)。

VM は handler の effect の引数の型の註を install の時に読み(doeff_vm._effect_types・SPEC-WITHHANDLER-TYPE-FILTER)、型の外の effect では
その handler を Python に入らずに外へ渡す。core-effects の handler は、どれも扱う型の外の effect を最初に `Pass` で外へ渡すだけなので、
註を足しても答えと順は変わらず、handler の本体に入る回数だけが減る。
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from types import CodeType, FrameType

import pytest
from doeff_core_effects import cache_handlers
from doeff_core_effects.effects import Ask, Await, Get, Listen, Local, Put, Slog, Try, WriterTellEffect
from doeff_core_effects.handlers import (
    await_handler,
    env_var_ask,
    lazy_ask,
    listen_handler,
    local_handler,
    reader,
    slog_discard_handler,
    slog_handler,
    state,
    try_handler,
    writer,
)
from doeff_vm._effect_types import handler_effect_types

from doeff import do, with_handlers
from doeff import run as doeff_run


def _raw(installed: object) -> Callable[..., object]:
    """Program -> Program の handler の値が包む、VM が呼ぶ handler の関数(doeff.program.handler が置く欄)。"""
    return installed.__doeff_handler_data__  # type: ignore[attr-defined] — doeff.program.handler が置く欄(型の無い欄)


@pytest.mark.parametrize(
    ("installed", "types"),
    [
        (reader({}), (Ask,)),
        (state(), (Get, Put)),
        (writer, (WriterTellEffect,)),
        (try_handler, (Try,)),
        (slog_handler, (Slog,)),
        (slog_discard_handler, (Slog,)),
        (local_handler, (Local,)),
        (listen_handler, (Listen,)),
        (await_handler(), (Await,)),
        (lazy_ask({}), (Ask, Local)),
        (env_var_ask(), (Ask,)),
        (
            cache_handlers.in_memory_cache_handler(),
            (
                cache_handlers.CacheGetEffect,
                cache_handlers.CacheExistsEffect,
                cache_handlers.CachePutEffect,
                cache_handlers.CacheDeleteEffect,
            ),
        ),
    ],
)
def test_each_handler_declares_the_types_it_answers(installed: object, types: tuple[type, ...]) -> None:
    assert handler_effect_types(_raw(installed)) == types


def _entries(code: CodeType, run: Callable[[], object]) -> tuple[int, object]:
    """run の間に code の関数が始まった回数と、run の答え。本体は generator なので再開ごとにも call の事象が出る — frame の同一性で数える。"""
    frames: dict[int, FrameType] = {}

    def profile(frame: FrameType, event: str, _arg: object) -> None:
        if event == "call" and frame.f_code is code:
            frames.setdefault(id(frame), frame)

    sys.setprofile(profile)
    try:
        result = run()
    finally:
        sys.setprofile(None)
    return len(frames), result


@do
def _asks_then_state():
    a = yield Ask("a")
    b = yield Ask("b")
    c = yield Ask("c")
    yield Put("n", a + b + c)
    return (yield Get("n"))


def test_an_effect_outside_the_types_does_not_enter_the_handler() -> None:
    # 列の最後が一番内側: Ask は state を飛ばして外の reader に届く
    stack = [reader({"a": 1, "b": 2, "c": 3}), state()]
    body = _raw(stack[1]).__wrapped__.__code__  # type: ignore[attr-defined] — @do の functools.wraps が置く欄
    entered, result = _entries(body, lambda: doeff_run(with_handlers(stack, _asks_then_state())))
    assert result == 6
    assert entered == 2, "state の本体に入るのは Put と Get の 2 回だけ(Ask 3 回は飛ばす)"


def test_the_answers_and_the_types_it_answers_are_unchanged() -> None:
    # 扱う型は今までどおり届く・註の無い素の handler は全部の effect を受ける。
    @do
    def passes_everything(effect, k):  # 型の註の無い素の handler
        from doeff import Pass

        yield Pass(effect, k)

    stack = [reader({"a": 1, "b": 2, "c": 3}), state(), passes_everything]
    code = passes_everything.__wrapped__.__code__  # type: ignore[attr-defined] — @do の functools.wraps が置く欄
    entered, result = _entries(code, lambda: doeff_run(with_handlers(stack, _asks_then_state())))
    assert result == 6
    assert entered == 5, "型の註の無い handler は Ask 3・Put・Get の 5 回とも受ける"
