"""PyKleisli values stored in reader env return None when retrieved via Ask.

Plain callables survive env round-trip through Ask, but PyKleisli
(@do decorated) values come back as None. This affects both Local env
and run() env parameter, regardless of Spawn.
"""

from __future__ import annotations

from doeff import Ask, Gather, Local, Spawn, do, default_handlers, run


@do
def _kleisli_func(x: int):
    return x * 10
    yield


def _plain_func(x: int) -> int:
    return x * 10


class TestAskEnvPyKleisli:
    def test_plain_callable_in_env_via_ask(self) -> None:
        @do
        def program():
            func = yield Ask("injected")
            return func(42)

        result = run(program(), handlers=default_handlers(), env={"injected": _plain_func})
        assert result.is_ok(), result.display()
        assert result.value == 420

    def test_pykleisli_in_env_via_ask_returns_none(self) -> None:
        @do
        def program():
            func = yield Ask("injected")
            assert func is not None, "PyKleisli was None after Ask from reader env"
            result = yield func(42)
            return result

        result = run(program(), handlers=default_handlers(), env={"injected": _kleisli_func})
        assert result.is_ok(), f"PyKleisli in env should survive Ask round-trip: {result.display()}"
        assert result.value == 420

    def test_pykleisli_in_local_env_via_ask_returns_none(self) -> None:
        @do
        def program():
            func = yield Ask("injected")
            assert func is not None, "PyKleisli was None after Ask from Local env"
            result = yield func(42)
            return result

        env = {"injected": _kleisli_func}
        result = run(Local(env, program()), handlers=default_handlers())
        assert result.is_ok(), (
            f"PyKleisli in Local env should survive Ask round-trip: {result.display()}"
        )
        assert result.value == 420


class TestSpawnEnvPyKleisli:
    def test_plain_callable_survives_spawn(self) -> None:
        @do
        def child():
            func = yield Ask("injected")
            return func(42)

        @do
        def parent():
            t = yield Spawn(child())
            results = yield Gather(t)
            return results[0]

        result = run(Local({"injected": _plain_func}, parent()), handlers=default_handlers())
        assert result.is_ok(), result.display()
        assert result.value == 420

    def test_pykleisli_in_spawn_returns_none(self) -> None:
        @do
        def child():
            func = yield Ask("injected")
            assert func is not None, "PyKleisli was None in spawned task"
            result = yield func(42)
            return result

        @do
        def parent():
            t = yield Spawn(child())
            results = yield Gather(t)
            return results[0]

        result = run(Local({"injected": _kleisli_func}, parent()), handlers=default_handlers())
        assert result.is_ok(), f"PyKleisli should survive Spawn env propagation: {result.display()}"
        assert result.value == 420
