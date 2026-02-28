import inspect

from doeff import EffectBase, Pass, Resume, WithHandler, do, run
from doeff.kleisli import validate_do_handler_effect_annotation
from doeff.program import _is_effect_annotation_kind


class _SubtypeEffect(EffectBase):
    def __init__(self, value: int) -> None:
        super().__init__()
        self.value = value


@do
def _body():
    return (yield _SubtypeEffect(7))


def test_effect_annotation_is_runtime_type_object_for_do_handler() -> None:
    @do
    def handler(effect: _SubtypeEffect, k):
        if isinstance(effect, _SubtypeEffect):
            return (yield Resume(k, effect.value))
        yield Pass()

    signature = inspect.signature(handler)
    effect_annotation = signature.parameters["effect"].annotation

    assert effect_annotation is _SubtypeEffect
    assert not isinstance(effect_annotation, str)
    assert _is_effect_annotation_kind(effect_annotation)


def test_validate_do_handler_effect_annotation_accepts_effect_subclass() -> None:
    @do
    def handler(effect: _SubtypeEffect, k):
        if isinstance(effect, _SubtypeEffect):
            return (yield Resume(k, effect.value))
        yield Pass()

    validate_do_handler_effect_annotation(handler)

    result = run(WithHandler(handler, _body()), handlers=[])
    assert result.is_ok()
    assert result.value == 7


def test_validate_do_handler_effect_annotation_resolves_quoted_subclass() -> None:
    @do
    def handler(effect: "_SubtypeEffect", k):
        if isinstance(effect, _SubtypeEffect):
            return (yield Resume(k, effect.value))
        yield Pass()

    validate_do_handler_effect_annotation(handler)
