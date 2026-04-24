from __future__ import annotations

import warnings

import pytest

from doeff import Resume, do


def _non_tail_resume_warnings(caught: list[warnings.WarningMessage]) -> list[warnings.WarningMessage]:
    return [warning for warning in caught if "non-tail Resume/ResumeThrow" in str(warning.message)]


def test_tail_resume_shape_does_not_warn() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")

        @do
        def handler(_effect: object, k: object):
            return (yield Resume(k, "value"))

    assert callable(handler)
    assert _non_tail_resume_warnings(caught) == []


def test_assignment_return_resume_shape_does_not_warn() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")

        @do
        def handler(_effect: object, k: object):
            value = yield Resume(k, "value")
            return value

    assert callable(handler)
    assert _non_tail_resume_warnings(caught) == []


def test_non_tail_resume_warns_without_marker() -> None:
    with pytest.warns(RuntimeWarning, match="non-tail Resume/ResumeThrow"):

        @do
        def handler(_effect: object, k: object):
            value = yield Resume(k, "value")
            return (value, "after")

    assert callable(handler)


def test_non_tail_resume_marker_suppresses_warning() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")

        @do(non_tail=True)
        def handler(_effect: object, k: object):
            value = yield Resume(k, "value")
            return (value, "after")

    assert callable(handler)
    assert _non_tail_resume_warnings(caught) == []


def test_protected_tail_looking_resume_warns_without_marker() -> None:
    with pytest.warns(RuntimeWarning, match="non-tail Resume/ResumeThrow"):

        @do
        def handler(_effect: object, k: object):
            try:
                return (yield Resume(k, "value"))
            finally:
                pass

    assert callable(handler)
