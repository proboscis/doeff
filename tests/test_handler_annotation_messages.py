"""A handler's effect annotation that no effect can match is refused where the handler is made,
and an UnhandledEffect names the handlers an annotation skipped and how to install one.

Before (first-look error table 2026-10-17, row 9 and the appendix; 0.5.0 and main 4685dc801d62):

- ``def fetch_handler(effect: str, k)`` was installed, the VM skipped it for every effect (no
  effect is a ``str``) and the run ended with ``UnhandledEffect: no handler found for effect:
  Fetch (...)`` / ``handlers in scope (innermost→outermost): fetch_handler`` — nothing said the
  annotation was the mistake (cases/e29_handler_wrong_annotation_type).
- an effect class of the user's own with no handler ended with ``unhandled effect: Fetch (...)
  (no handlers in scope)``; only the core effects had a hint line
  (cases/e23_custom_effect_unhandled).

The annotation that cannot be evaluated keeps its RuntimeWarning and its fallback to every effect
(cases/e28_future_annotations_typed_handler), and a marker base that is not an EffectBase keeps
working as a filter (docs/02-core-concepts.md "Catching a family of effects by their parent type").
"""

import sys
import warnings
from dataclasses import dataclass
from typing import Annotated, Any, Protocol, runtime_checkable

import pytest
from doeff_vm import WithHandler

from doeff import (
    Effect,
    EffectBase,
    Pass,
    Resume,
    UnhandledEffect,
    do,
    handler,
    run,
    with_handlers,
)


@dataclass(frozen=True)
class Fetch(EffectBase):
    url: str


@dataclass(frozen=True)
class Store(EffectBase):
    key: str


@dataclass(frozen=True)
class Other(EffectBase):
    value: int


class MyError(Exception):
    pass


@do
def fetch_program():
    return (yield Fetch("https://example.com"))


@do
def catch_all(effect: Effect, k):
    return (yield Resume(k, f"fallback:{type(effect).__name__}"))


def _annotated(annotation):
    """A plain handler whose effect parameter carries ``annotation`` (a type or its text)."""

    def annotated_handler(effect, k):
        return Resume(k, f"got {effect}")

    annotated_handler.__annotations__ = {"effect": annotation}
    return annotated_handler


def _message(program) -> str:
    with pytest.raises(UnhandledEffect) as excinfo:
        run(program)
    return str(excinfo.value)


# --- e29: the annotation names a type no effect can be an instance of ---------------------------


@do
def fetch_handler(effect: str, k):
    return (yield Resume(k, f"got {effect}"))


_REFUSAL = (
    "the annotation of effect parameter 'effect' names str, which no effect can be an instance "
    "of, so the handler would never be called. Annotate it with the effect class it handles "
    "(a subclass of EffectBase, e.g. effect: MyEffect), or with EffectBase to receive every effect."
)


class TestAnnotationThatNoEffectMatchesIsRefused:
    def test_handler_refuses_a_str_annotation_where_it_is_made(self):
        with pytest.raises(TypeError) as excinfo:
            handler(fetch_handler)
        message = str(excinfo.value)
        assert message.startswith("handler fetch_handler (defined at ")
        assert f"{__file__}:" in message
        assert message.endswith(_REFUSAL)

    def test_with_handlers_refuses_it_too(self):
        with pytest.raises(TypeError, match="names str, which no effect can be an instance of"):
            with_handlers([fetch_handler], fetch_program())

    def test_a_raw_with_handler_is_refused_when_it_is_installed(self):
        with pytest.raises(TypeError, match="names str, which no effect can be an instance of"):
            run(WithHandler(fetch_handler, fetch_program()))

    @pytest.mark.parametrize(
        ("annotation", "named"),
        [
            (int, "int"),
            (dict, "dict"),
            (bytes, "bytes"),
            (Exception, "Exception"),
            (MyError, "MyError"),
            (list[str], "list"),
            (Annotated[str, "doc"], "str"),
            (Fetch | str, "str"),
            (Fetch | None, "NoneType"),
            ("str", "str"),
        ],
        ids=lambda value: value if isinstance(value, str) else None,
    )
    def test_each_type_no_effect_can_be_is_refused(self, annotation, named):
        with pytest.raises(TypeError, match=f"names {named}, which no effect can be an instance of"):
            handler(_annotated(annotation))


# --- what stays accepted ------------------------------------------------------------------------


class Marker:
    """A marker base that is not an EffectBase; no effect class inherits it when it is installed."""


@runtime_checkable
class HasUrl(Protocol):
    url: str


class TestAnnotationsThatStayAccepted:
    @pytest.mark.parametrize("annotation", [Fetch, Fetch | Store, EffectBase, Effect, Any, object])
    def test_effect_types_and_catch_alls_are_accepted(self, annotation):
        handler(_annotated(annotation))

    def test_a_marker_base_is_a_filter_even_before_an_effect_inherits_it(self):
        @do
        def marked(effect: Marker, k):
            return (yield Resume(k, f"marked:{type(effect).__name__}"))

        install = handler(marked)

        @dataclass(frozen=True)
        class Tagged(EffectBase, Marker):
            name: str

        @do
        def body():
            return ((yield Tagged("t")), (yield Other(1)))

        assert run(with_handlers([catch_all, install], body())) == ("marked:Tagged", "fallback:Other")

    def test_a_runtime_checkable_protocol_is_a_filter(self):
        @do
        def with_url(effect: HasUrl, k):
            return (yield Resume(k, f"url:{effect.url}"))

        @do
        def body():
            return ((yield Fetch("u")), (yield Other(1)))

        assert run(with_handlers([catch_all, with_url], body())) == ("url:u", "fallback:Other")

    def test_an_annotation_that_cannot_be_evaluated_still_warns_and_takes_every_effect(self):
        """e28: the text of the annotation names a class the handler's module does not hold."""
        unresolved = _annotated("LocalFetch")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = run(handler(unresolved)(fetch_program()))
        assert result == "got Fetch(url='https://example.com')"
        assert [str(w.message) for w in caught if w.category is RuntimeWarning] == [
            f"handler {unresolved.__qualname__}: the annotation of effect parameter 'effect' "
            "cannot be evaluated at runtime, so the handler receives every effect (no type "
            "filter). Import the effect type at runtime to get the filter."
        ]

    def test_a_name_defined_after_handler_resolves_at_the_install(self, monkeypatch):
        """handler() does not settle an annotation it cannot evaluate yet: the install does."""
        later = _annotated("_DefinedAfterHandler")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            install = handler(later)
        assert caught == []

        monkeypatch.setattr(sys.modules[__name__], "_DefinedAfterHandler", Fetch, raising=False)

        @do
        def body():
            return ((yield Fetch("u")), (yield Other(1)))

        assert run(with_handlers([catch_all], install(body()))) == (
            "got Fetch(url='u')",
            "fallback:Other",
        )


# --- the UnhandledEffect names the handlers an annotation skipped -------------------------------


_SKIPPED_TAIL = " — a handler only sees the effects its effect parameter is annotated with"


@do
def store_handler(effect: Store, k):
    return (yield Resume(k, None))


@do
def store_or_other(effect: Store | Other, k):
    return (yield Resume(k, None))


@do
def pass_through(effect, k):
    yield Pass(effect, k)


@do
def fetch_passer(effect: Fetch, k):
    yield Pass(effect, k)


class TestUnhandledEffectNamesTheSkippedHandlers:
    def test_the_skipped_handler_and_its_annotation_follow_the_chain_line(self):
        lines = _message(handler(store_handler)(fetch_program())).splitlines()
        assert lines[0] == "no handler found for effect: Fetch (Fetch(url='https://example.com'))"
        assert lines[1] == "  handlers in scope (innermost→outermost): store_handler"
        assert lines[2] == "  skipped by annotation: store_handler (effect: Store)" + _SKIPPED_TAIL
        assert lines[3].startswith("  hint: ")

    def test_every_skipped_handler_is_named_innermost_first(self):
        program = with_handlers([store_or_other, pass_through, store_handler], fetch_program())
        lines = _message(program).splitlines()
        assert lines[2] == (
            "  skipped by annotation: store_handler (effect: Store), "
            "store_or_other (effect: Store | Other)" + _SKIPPED_TAIL
        )

    def test_a_handler_that_saw_the_effect_is_not_named(self):
        message = _message(with_handlers([fetch_passer, pass_through], fetch_program()))
        assert "skipped by annotation" not in message

    def test_a_class_of_the_same_name_is_told_apart_by_its_module(self):
        """The handler names another class called Fetch (a module imported twice, as __main__
        and by its name): the line says which Fetch each is."""
        other_fetch = type("Fetch", (EffectBase,), {"__module__": "elsewhere"})
        program = handler(_annotated(other_fetch))(fetch_program())
        lines = _message(program).splitlines()
        assert lines[2] == (
            "  skipped by annotation: _annotated (effect: elsewhere.Fetch, not the effect's "
            f"{__name__}.Fetch)" + _SKIPPED_TAIL
        )


# --- e23: an effect of the user's own with no handler ends with how to install one --------------


_GENERAL_HINT = (
    "  hint: install a handler for Fetch: run(handler(fn)(prog)), where fn is a @do function "
    "(effect: Fetch, k) that returns (yield Resume(k, value))"
)


class TestUserEffectHint:
    def test_no_handler_at_all_ends_with_how_to_install_one(self):
        assert _message(fetch_program()) == (
            "unhandled effect: Fetch (Fetch(url='https://example.com')) (no handlers in scope)\n"
            + _GENERAL_HINT
        )

    def test_handlers_that_passed_it_end_with_the_same_hint(self):
        assert _message(handler(pass_through)(fetch_program())).splitlines()[-1] == _GENERAL_HINT

    def test_following_the_hint_runs_the_program(self):
        @do
        def fn(effect: Fetch, k):
            value = f"body of {effect.url}"
            return (yield Resume(k, value))

        assert run(handler(fn)(fetch_program())) == "body of https://example.com"
