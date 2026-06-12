"""handler() must reject non-callable raw handlers at construction time."""

import pytest

from doeff import do
from doeff import handler as make_handler


@do
def _body():
    return 42


def test_handler_rejects_non_callable_int():
    with pytest.raises(TypeError, match="callable"):
        make_handler(42)


def test_handler_rejects_non_callable_string():
    with pytest.raises(TypeError, match="callable"):
        make_handler("hello")


def test_handler_rejects_non_callable_none():
    with pytest.raises(TypeError, match="callable"):
        make_handler(None)


def test_handler_accepts_do_handler():
    @do
    def raw_handler(effect, k):
        from doeff import Pass
        yield Pass(effect, k)

    ctrl = make_handler(raw_handler)(_body())
    assert ctrl.handler is raw_handler


def test_handler_accepts_plain_callable():
    def raw_handler(effect, k):
        from doeff import Resume
        return Resume(k, "ok")

    ctrl = make_handler(raw_handler)(_body())
    assert ctrl.handler is raw_handler


def test_handler_is_idempotent_for_installed_handler():
    @do
    def raw_handler(effect, k):
        from doeff import Pass
        yield Pass(effect, k)

    installed = make_handler(raw_handler)
    assert make_handler(installed) is installed
