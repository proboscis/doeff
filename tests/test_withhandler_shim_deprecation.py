"""PR A4: WithHandler shim emits a DeprecationWarning.

The shim itself stays in place (scope A — permanent shim for the legacy
@do-dispatcher form). Calling ``WithHandler(h, body)`` surfaces a
DeprecationWarning that points callers at the new-style idiom:

  # Before
  WithHandler(my_handler, program)
  # After (new-style defhandler / handle)
  my_handler(program)

``WithHandlerType`` (the type alias for ``isinstance`` checks and direct
DoExpr construction) is *not* deprecated — only the wrapping function.
"""

from __future__ import annotations

import warnings

from doeff import Pass, Pure, WithHandler, WithHandlerType, do, run
from doeff.program import _WithHandlerNode


def _new_style_handler():
    """Build a minimal new-style handler: a Program -> Program function
    with the PR A1 marker."""

    def handler_fn(body):
        return _WithHandlerNode(_legacy_dispatcher, body)

    handler_fn._doeff_is_handler_fn = True  # type: ignore[attr-defined]
    return handler_fn


@do
def _legacy_dispatcher(effect, k):
    yield Pass(effect, k)


class TestWithHandlerDeprecation:
    def test_shim_emits_deprecation_warning_for_new_style(self):
        fn = _new_style_handler()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always", DeprecationWarning)
            WithHandler(fn, Pure(1))
        dep = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert dep, "expected DeprecationWarning"
        msg = str(dep[0].message)
        assert "WithHandler" in msg
        # The warning should show the recommended replacement.
        assert "h(body)" in msg or "h(program)" in msg

    def test_shim_emits_deprecation_warning_for_legacy_dispatcher(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always", DeprecationWarning)
            WithHandler(_legacy_dispatcher, Pure(1))
        dep = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert dep, "expected DeprecationWarning"
        msg = str(dep[0].message)
        assert "WithHandler" in msg
        # Legacy path gets a second pointer to defhandler.
        assert "defhandler" in msg


class TestShimStillWorks:
    """Deprecation is soft — calls continue to succeed."""

    def test_new_style_routes_to_handler_call(self):
        fn = _new_style_handler()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            result = WithHandler(fn, Pure(42))
        # The result should be whatever fn(Pure(42)) returned, which in
        # our stub is a _WithHandlerNode.
        assert isinstance(result, _WithHandlerNode)

    def test_legacy_returns_node(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            node = WithHandler(_legacy_dispatcher, Pure(7))
        assert isinstance(node, _WithHandlerNode)

    def test_legacy_full_run_still_works(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            body = WithHandler(_legacy_dispatcher, Pure(123))
        assert run(body) == 123


class TestWithHandlerTypeNotDeprecated:
    def test_type_alias_usable_without_warning(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always", DeprecationWarning)
            node = WithHandlerType(_legacy_dispatcher, Pure(1))
        dep = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert not dep, "WithHandlerType should not warn"
        assert isinstance(node, _WithHandlerNode)
