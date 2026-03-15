"""End-to-end coverage for @do auto-unwrap annotation variations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from doeff import Ask, Effect, EffectBase, Program, ProgramBase, default_handlers, do, run
from doeff.effects._program_types import ProgramLike
from doeff.program import DoCtrl, DoExpr


@dataclass(frozen=True)
class ProbeEffect(EffectBase):
    payload: str


@do
def produce_number(value: int):
    return value * 10


@do
def produce_text(value: str):
    return value


def _run_value(program: Program[Any], *, env: dict[str, Any] | None = None) -> Any:
    return run(program, handlers=default_handlers(), env=env).value


def _assert_program_object(value: object) -> None:
    assert isinstance(value, (ProgramBase, DoCtrl)), (
        f"expected Program object, got {type(value).__name__}: {value!r}"
    )


def _assert_effect_object(value: object) -> None:
    assert isinstance(value, EffectBase), (
        f"expected Effect object, got {type(value).__name__}: {value!r}"
    )


def test_program_varargs_annotation_preserves_program_objects() -> None:
    @do
    def inspect_programs(*programs: Program[int]):
        for program in programs:
            _assert_program_object(program)

        values = []
        for program in programs:
            values.append((yield program))
        return values

    assert _run_value(inspect_programs(produce_number(1), produce_number(2))) == [10, 20]


def test_doexpr_varargs_annotation_preserves_program_objects() -> None:
    @do
    def inspect_expressions(*expressions: DoExpr[str]):
        for expression in expressions:
            _assert_program_object(expression)

        values = []
        for expression in expressions:
            values.append((yield expression))
        return values

    assert _run_value(inspect_expressions(produce_text("alpha"), produce_text("beta"))) == [
        "alpha",
        "beta",
    ]


def test_programlike_varargs_annotation_preserves_program_and_effect_objects() -> None:
    @do
    def inspect_args(*args: ProgramLike):
        _assert_program_object(args[0])
        _assert_effect_object(args[1])

        first = yield args[0]
        second = yield args[1]
        return [first, second]

    assert _run_value(
        inspect_args(produce_text("alpha"), Ask("name")),
        env={"name": "beta"},
    ) == ["alpha", "beta"]


def test_program_union_with_none_annotation_preserves_program_object() -> None:
    @do
    def inspect_program(program: Program[int] | None):
        if program is None:
            return "missing"

        _assert_program_object(program)
        return (yield program)

    assert _run_value(inspect_program(produce_number(3))) == 30
    assert _run_value(inspect_program(None)) == "missing"


def test_annotated_program_annotation_preserves_program_object() -> None:
    @do
    def inspect_program(program: Annotated[Program[int], "opaque"]):
        _assert_program_object(program)
        return (yield program)

    assert _run_value(inspect_program(produce_number(4))) == 40


def test_effect_varargs_annotation_preserves_effect_objects() -> None:
    @do
    def inspect_effects(*effects: Effect):
        for effect in effects:
            _assert_effect_object(effect)

        values = []
        for effect in effects:
            values.append((yield effect))
        return values

    assert _run_value(
        inspect_effects(Ask("first"), Ask("second")),
        env={"first": "left", "second": "right"},
    ) == ["left", "right"]


def test_effect_subclass_annotation_preserves_custom_effect_object() -> None:
    @do
    def inspect_effect(effect: ProbeEffect):
        assert isinstance(effect, ProbeEffect)
        return effect.payload

    assert _run_value(inspect_effect(ProbeEffect("payload"))) == "payload"


def test_future_annotations_are_resolved_through_the_do_pipeline() -> None:
    @do
    def inspect_program(program: Program[int]):
        _assert_program_object(program)
        return (yield program)

    raw_annotation = inspect_program.__annotations__["program"]
    assert isinstance(raw_annotation, str)
    assert "Program[int]" in raw_annotation
    assert _run_value(inspect_program(produce_number(5))) == 50


def test_unannotated_varargs_auto_unwrap_programlike_values() -> None:
    @do
    def collect_values(*args):
        assert args == ("alpha", "beta")
        return list(args)

    assert _run_value(
        collect_values(produce_text("alpha"), Ask("name")),
        env={"name": "beta"},
    ) == ["alpha", "beta"]


def test_any_varargs_auto_unwrap_programlike_values() -> None:
    @do
    def collect_values(*args: Any):
        assert args == ("alpha", "beta")
        return list(args)

    assert _run_value(
        collect_values(produce_text("alpha"), Ask("name")),
        env={"name": "beta"},
    ) == ["alpha", "beta"]


def test_str_varargs_auto_unwrap_programlike_values() -> None:
    @do
    def collect_values(*args: str):
        assert args == ("alpha", "beta")
        return list(args)

    assert _run_value(
        collect_values(produce_text("alpha"), Ask("name")),
        env={"name": "beta"},
    ) == ["alpha", "beta"]
