"""WithHandler must reject non-callable handlers at construction time."""

import pytest

from doeff import WithHandler, do


@do
def _body():
    return 42


def test_withhandler_rejects_non_callable_int():
    with pytest.raises(TypeError, match="callable"):
        WithHandler(42, _body())


def test_withhandler_rejects_non_callable_string():
    with pytest.raises(TypeError, match="callable"):
        WithHandler("hello", _body())


def test_withhandler_rejects_non_callable_none():
    with pytest.raises(TypeError, match="callable"):
        WithHandler(None, _body())


def test_withhandler_accepts_do_handler():
    @do
    def handler(effect, k):
        from doeff import Pass
        yield Pass()

    ctrl = WithHandler(handler, _body())
    assert ctrl.handler is handler


def test_withhandler_accepts_plain_callable():
    def handler(effect, k):
        from doeff import Resume
        return Resume(k, "ok")

    ctrl = WithHandler(handler, _body())
    assert ctrl.handler is handler
