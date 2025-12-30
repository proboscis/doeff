"""Regression tests ensuring KleisliProgram metadata plays nicely with beartype."""

from __future__ import annotations

from beartype import beartype

from doeff import Program
from doeff.kleisli import KleisliProgram, P


def test_kleisli_annotations_bind_paramspec_args() -> None:
    """The resolved annotations expose the concrete ParamSpec helpers."""

    annotations = KleisliProgram.__call__.__annotations__

    assert annotations["args"].__origin__ is P
    assert annotations["kwargs"].__origin__ is P


def test_kleisli_call_is_beartype_decoratable() -> None:
    """Applying ``@beartype`` to ``KleisliProgram.__call__`` should succeed."""

    decorated_call = beartype(KleisliProgram.__call__)

    kleisli = KleisliProgram(lambda: Program.pure(None))

    result = decorated_call(kleisli)

    assert isinstance(result, Program)
