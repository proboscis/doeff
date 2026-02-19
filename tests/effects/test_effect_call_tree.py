"""Tests for effect call tree visualisation."""

from __future__ import annotations

from doeff import do
from doeff.analysis import EffectCallTree
from doeff.types import CallFrame, EffectObservation


@do
def outer(x: int):
    return x


@do
def inner(y: int):
    return y


def _make_frame(kleisli, name: str, *args, depth: int = 0, **kwargs):
    return CallFrame(
        kleisli=kleisli,
        function_name=name,
        args=args,
        kwargs=kwargs,
        depth=depth,
    )


def test_builds_tree_from_observations():
    frame_outer = _make_frame(outer, "outer", 1, depth=0)
    frame_inner = _make_frame(inner, "inner", depth=1)

    observations = [
        EffectObservation(
            effect_type="Ask",
            key="value",
            context=None,
            call_stack_snapshot=(frame_outer, frame_inner),
        )
    ]

    tree = EffectCallTree.from_observations(observations)
    output = tree.visualize_ascii()

    assert "outer(1)" in output
    assert "inner()" in output
    assert "Ask('value')" in output


def test_aggregates_multiple_effects():
    frame_outer = _make_frame(outer, "outer", depth=0)

    observations = [
        EffectObservation(
            effect_type="WriterTell",
            key=None,
            context=None,
            call_stack_snapshot=(frame_outer,),
        )
        for _ in range(3)
    ]

    tree = EffectCallTree.from_observations(observations)
    output = tree.visualize_ascii()

    assert "WriterTell x3" in output


def test_empty_tree_visualizes_as_placeholder():
    tree = EffectCallTree.from_observations([])
    assert tree.visualize_ascii() == "(no effects)"
