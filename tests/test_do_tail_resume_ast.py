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


def test_do_decorator_handles_unparseable_source() -> None:
    """Hy-defined handlers and other non-Python source bodies must not crash @do.

    inspect.getsourcelines on a Hy function returns the Hy source verbatim,
    which Python's tokenizer rejects with tokenize.TokenError. The tail-resume
    analysis is purely diagnostic and must silently skip when source cannot be
    parsed — runtime behavior of the wrapped generator is unchanged.
    """
    import inspect
    import tokenize

    from doeff.do import _analyze_resume_yields

    def real_handler(_effect: object, k: object):
        yield Resume(k, "value")

    real_getsourcelines = inspect.getsourcelines

    def raising_getsourcelines(fn):
        if fn is real_handler:
            raise tokenize.TokenError("unexpected EOF in multi-line statement", (81, 0))
        return real_getsourcelines(fn)

    inspect.getsourcelines = raising_getsourcelines
    try:
        result = _analyze_resume_yields(real_handler, non_tail=False)
    finally:
        inspect.getsourcelines = real_getsourcelines

    assert result == ()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        inspect.getsourcelines = raising_getsourcelines
        try:
            wrapped = do(real_handler)
        finally:
            inspect.getsourcelines = real_getsourcelines

    assert callable(wrapped)
    assert _non_tail_resume_warnings(caught) == []
