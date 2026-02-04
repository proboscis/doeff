import pytest

from doeff.cesk_v3.level1_cesk.state import (
    CESKState,
    ProgramControl,
    Value,
    Error,
    EffectYield,
)
from doeff.cesk_v3.level1_cesk.frames import ReturnFrame
from doeff.cesk_v3.level1_cesk.step import cesk_step


class TestGeneratorProtocol:
    def test_next_called_on_new_generator(self, empty_env, empty_store, empty_k):
        call_count = [0]

        def program_gen():
            call_count[0] += 1
            yield "first"

        state = CESKState(
            C=ProgramControl(program=program_gen),
            E=empty_env,
            S=empty_store,
            K=empty_k,
        )

        cesk_step(state)

        assert call_count[0] == 1

    def test_send_called_on_resumed_generator(self, empty_env, empty_store):
        sent_value = []

        def program_gen():
            v = yield "effect"
            sent_value.append(v)
            return v

        gen = program_gen()
        next(gen)

        state = CESKState(
            C=Value("hello"),
            E=empty_env,
            S=empty_store,
            K=[ReturnFrame(generator=gen)],
        )

        cesk_step(state)

        assert sent_value == ["hello"]

    def test_throw_called_on_error(self, empty_env, empty_store):
        thrown_error = []

        def program_gen():
            try:
                yield "effect"
            except Exception as e:
                thrown_error.append(e)
                raise

        gen = program_gen()
        next(gen)

        exc = ValueError("thrown")
        state = CESKState(
            C=Error(exc),
            E=empty_env,
            S=empty_store,
            K=[ReturnFrame(generator=gen)],
        )

        cesk_step(state)

        assert len(thrown_error) == 1
        assert thrown_error[0] is exc


class TestGeneratorCallable:
    def test_callable_converted_to_generator(self, empty_env, empty_store, empty_k):
        def program_func():
            yield 42
            return "done"

        state = CESKState(
            C=ProgramControl(program=program_func),
            E=empty_env,
            S=empty_store,
            K=empty_k,
        )

        result = cesk_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, EffectYield)
        assert result.C.yielded == 42

    def test_generator_instance_used_directly(self, empty_env, empty_store, empty_k):
        def program_func():
            yield "effect"
            return "done"

        gen = program_func()

        state = CESKState(
            C=ProgramControl(program=gen),
            E=empty_env,
            S=empty_store,
            K=empty_k,
        )

        result = cesk_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, EffectYield)
        assert result.C.yielded == "effect"
