from doeff import HandlerGet, HandlerHas, HandlerSet, Pass, Resume, WithHandler, default_handlers, do, run
from doeff.effects.base import Effect
from doeff.effects.base import EffectBase


class CountFx(EffectBase):
    pass


@do
def _counting_handler(effect: Effect, k: object):
    if not isinstance(effect, CountFx):
        yield Pass()
        return None

    if not (yield HandlerHas("counter")):
        yield HandlerSet("counter", 0)

    current = yield HandlerGet("counter")
    next_value = current + 1
    yield HandlerSet("counter", next_value)
    return (yield Resume(k, next_value))


def test_handler_local_state_is_shared_within_installation() -> None:
    @do
    def body():
        first = yield CountFx()
        second = yield CountFx()
        return (first, second)

    result = run(WithHandler(_counting_handler, body()), handlers=default_handlers())
    assert result.is_ok(), result.display()
    assert result.value == (1, 2)


def test_handler_local_state_shadows_per_installation() -> None:
    @do
    def body():
        outer_first = yield CountFx()
        inner = yield WithHandler(_counting_handler, CountFx())
        outer_second = yield CountFx()
        return (outer_first, inner, outer_second)

    result = run(WithHandler(_counting_handler, body()), handlers=default_handlers())
    assert result.is_ok(), result.display()
    assert result.value == (1, 1, 2)
