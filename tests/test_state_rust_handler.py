"""state の答え手は VM の中の Rust の handler(doeff_vm.StateHandler)で、Get / Put に Python の handler なしで答える
(card ki-83236a21466e)。答え・外へ渡す effect・例外の出る所・handler の一覧の見え方が、Python の `@do` の handler だった時と同じ事を確かめる。
"""

from __future__ import annotations

import pytest
from doeff_core_effects.effects import Ask, Get, Put, Try
from doeff_core_effects.handlers import reader, state, try_handler
from doeff_core_effects.scheduler import Spawn, Wait, scheduled
from doeff_vm._effect_types import handler_effect_types
from doeff_vm.doeff_vm import StateHandler

from doeff import GetHandlers, Pass, Transfer, do, handler, run, with_handlers
from doeff.handler_utils import reinstaller


def _raw(installed: object) -> object:
    """state() の installer が包む、VM が呼ぶ handler の値(doeff.program が置く欄)。"""
    return installed.__doeff_handler_data__  # type: ignore[attr-defined] — installer の印の欄(型の無い欄)


@do
def _put_then_get():
    """Put の答えと、書いたキー・無いキーの Get の答えを返す。"""
    put = yield Put("a", 1)
    return put, (yield Get("a")), (yield Get("missing"))


def test_get_reads_what_put_wrote_and_none_for_a_missing_key() -> None:
    assert run(state()(_put_then_get())) == (None, 1, None)


def test_initial_state_is_read_and_not_shared_with_the_callers_mapping() -> None:
    initial = {"a": 10}

    @do
    def body():
        before = yield Get("a")
        yield Put("a", 11)
        return before

    assert run(state(initial)(body())) == 10
    assert initial == {"a": 10}


def test_each_state_call_has_its_own_store() -> None:
    @do
    def body():
        return (yield Get("a"))

    run(state()(_put_then_get()))
    assert run(state()(body())) is None


def test_a_subclass_of_get_and_put_is_answered() -> None:
    class MyGet(Get):
        pass

    class MyPut(Put):
        pass

    @do
    def body():
        yield MyPut("a", 5)
        return (yield MyGet("a"))

    assert run(state()(body())) == 5


def test_other_effects_pass_to_the_outer_handler() -> None:
    @do
    def body():
        yield Put("a", 1)
        return (yield Ask("k")), (yield Get("a"))

    assert run(with_handlers([reader({"k": "v"}), state()], body())) == ("v", 1)


def test_a_handler_inside_state_answers_the_get_of_a_key_first() -> None:
    """ADR-DOE-HY-006: session の値は state の効果を通るので、内の handler が同じキーの Get に答えて値を差し替え、Put を観測できる。"""
    seen: list[object] = []

    @handler
    def override(effect: Get | Put, k):
        if isinstance(effect, Get) and effect.key == "a":
            return (yield Transfer(k, "overridden"))
        if isinstance(effect, Put):
            seen.append((effect.key, effect.value))
        yield Pass(effect, k)

    @do
    def body():
        yield Put("a", 1)
        yield Put("b", 2)
        return (yield Get("a")), (yield Get("b"))

    assert run(with_handlers([state(), override], body())) == ("overridden", 2)
    assert seen == [("a", 1), ("b", 2)]


def test_an_unhashable_key_raises_at_the_yield_of_the_effect() -> None:
    @do
    def body():
        try:
            yield Get([])
        except TypeError as error:
            return f"caught: {error}"
        return "no error"

    # CPython の文は版で違う(3.14 から "cannot use 'list' as a dict key (unhashable type: 'list')")— 両方に在る部分で見る。
    caught = run(state()(body()))
    assert caught.startswith("caught: ") and "unhashable type: 'list'" in caught, caught


def test_a_raising_hash_of_a_put_key_raises_at_the_yield() -> None:
    class BadKey:
        def __hash__(self) -> int:
            raise RuntimeError("bad hash")

    @do
    def body():
        try:
            yield Put(BadKey(), 1)
        except RuntimeError as error:
            return str(error)
        return "no error"

    assert run(state()(body())) == "bad hash"


def test_get_handlers_answers_the_handler_object_state_made() -> None:
    installed = state()

    @handler
    def peek(effect: Ask, k):
        handlers = yield GetHandlers(k)
        return (yield Transfer(k, handlers))

    @do
    def body():
        return (yield Ask("x"))

    handlers = run(with_handlers([peek, installed], body()))
    assert handlers[0] is _raw(installed)
    assert [getattr(h, "__name__", None) for h in handlers] == ["handler", "peek"]
    assert handlers[0].__qualname__ == "state.<locals>.handler"


def test_a_reinstalled_state_handler_keeps_its_store() -> None:
    """try_handler は内の handler を GetHandlers で捕まえて Try の program の周りに据え直す — 据え直した state は同じ store を持つ。"""

    @do
    def inner():
        yield Put("a", (yield Get("a")) + 1)
        return "done"

    @do
    def body():
        yield Put("a", 1)
        result = yield Try(inner())
        return result, (yield Get("a"))

    result, value = run(with_handlers([try_handler, state()], body()))
    assert result.value == "done"
    assert value == 2


def test_reinstaller_puts_the_captured_state_handler_back() -> None:
    installed = state({"a": 3})

    @do
    def body():
        return (yield Get("a"))

    assert run(reinstaller(_raw(installed))(body())) == 3


def test_a_spawned_task_shares_the_store() -> None:
    @do
    def child():
        yield Put("a", "from child")

    @do
    def body():
        task = yield Spawn(child())
        yield Wait(task)
        return (yield Get("a"))

    assert run(scheduled(state()(body()))) == "from child"


def test_the_traceback_handler_chain_and_effect_types_read_as_before() -> None:
    assert handler_effect_types(_raw(state())) == (Get, Put)
    installed = state()
    assert installed.__name__ == "handler"
    assert installed.__qualname__ == "state.<locals>.handler"


def test_the_handler_object_is_not_called_from_python() -> None:
    with pytest.raises(TypeError, match="dispatched by the VM"):
        StateHandler({}, Get, Put)(Get("a"), None)
