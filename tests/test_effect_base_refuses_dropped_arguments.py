"""Check that EffectBase (alias doeff.Effect) rejects, with TypeError, arguments that would be kept nowhere (initiative doeff-publish).

Failure case (the docs example run through to the end in 0.5.0, report 2026-10-17-doeff-docs-137-run.html, MARKERS L37 and L245):
the example `yield Effect("fetch", url="...")` in docs/MARKERS.md runs silently in 0.5.0. EffectBase's `__new__` accepts and discards
all arguments for subclasses that have an `__init__` (dataclass effects), and EffectBase itself has neither `__init__` nor
`__dict__`, so both the name and the url vanish. The handler receives an empty EffectBase and cannot tell
fetch, process, and save apart.

The rejection condition is the same as CPython's object: "there is no `__init__` that accepts arguments": calling EffectBase directly, and calling
a subclass without `__init__` with arguments. Subclasses that have `__init__` (dataclass, hand-written `__init__`) and
calls without arguments (`Skip()`, pickle restore) pass as before.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass

import doeff_vm
import pytest

from doeff import Effect, EffectBase, Resume, do, handler, run


@dataclass(frozen=True)
class Fetch(EffectBase):
    url: str


class Marker(EffectBase):
    """An effect with neither fields nor `__init__` (same shape as Skip in doeff-traverse)."""


class Payload(EffectBase):
    """An effect that has fields through its own `__init__` (the shape in the chapter 13 API list)."""

    def __init__(self, payload: object) -> None:
        super().__init__()
        self.payload = payload


class Routed(EffectBase):
    """An effect that chooses the class to build in its own `__new__` and has fields through `__init__` (the shape of PublishEffect in doeff-events)."""

    def __new__(cls, event: object) -> Routed:
        return EffectBase.__new__(cls)

    def __init__(self, event: object) -> None:
        super().__init__()
        self.event = event


def test_effect_with_a_name_and_arguments_is_refused() -> None:
    # Exactly the shape of the old MARKERS example.
    with pytest.raises(TypeError, match=r"EffectBase\(\) takes no arguments"):
        Effect("fetch", url="api.example.com")


@pytest.mark.parametrize(
    ("args", "kwargs"),
    [(("fetch",), {}), ((), {"url": "api.example.com"}), (("save",), {"data": [1]})],
    ids=["positional", "keyword", "both"],
)
def test_effect_base_refuses_every_argument_it_would_drop(
    args: tuple[object, ...], kwargs: dict[str, object]
) -> None:
    with pytest.raises(TypeError, match=r"EffectBase\(\) takes no arguments"):
        doeff_vm.EffectBase(*args, **kwargs)


def test_the_refusal_says_where_the_arguments_belong() -> None:
    with pytest.raises(TypeError) as raised:
        Effect("fetch", url="api.example.com")
    message = str(raised.value)
    # What the reader writes next = an effect class with fields, and how to build it.
    assert "@dataclass(frozen=True)" in message, message
    assert "(EffectBase)" in message, message


def test_subclass_without_init_refuses_arguments_and_names_itself() -> None:
    with pytest.raises(TypeError, match=r"Marker\(\) takes no arguments"):
        Marker("fetch")
    with pytest.raises(TypeError, match=r"Marker\(\) takes no arguments"):
        Marker(url="api.example.com")


def test_calls_without_arguments_still_make_effects() -> None:
    assert type(doeff_vm.EffectBase()) is doeff_vm.EffectBase
    assert isinstance(Marker(), Marker)


def test_subclasses_with_an_init_keep_their_arguments() -> None:
    assert Fetch(url="api.example.com").url == "api.example.com"
    assert Fetch("api.example.com").url == "api.example.com"
    assert Payload({"id": 1}).payload == {"id": 1}
    assert Routed("ping").event == "ping"


def test_pickle_round_trip_still_rebuilds_effects() -> None:
    # EffectBase.__reduce_ex__ rebuilds via copyreg.__newobj__(cls) with no arguments.
    assert pickle.loads(pickle.dumps(Fetch(url="api.example.com"))) == Fetch(url="api.example.com")
    assert pickle.loads(pickle.dumps(Payload("x"))).payload == "x"
    assert isinstance(pickle.loads(pickle.dumps(Marker())), Marker)


def test_handler_reads_the_fields_of_an_effect_class() -> None:
    # The shape of the fixed MARKERS example: the arguments stay in the effect class fields and the handler can read them.
    @do
    def fetch_data():
        return (yield Fetch(url="api.example.com"))

    @handler
    def fake_fetch(effect: Fetch, k):
        return (yield Resume(k, f"response from {effect.url}"))

    assert run(handler(fake_fetch)(fetch_data())) == "response from api.example.com"
