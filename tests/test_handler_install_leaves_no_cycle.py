"""A handler built per call is freed by its reference count, not left for the cycle collector.

``handler(raw)`` and the boundary handlers built from it (``on_raise`` — the ``on-raise`` macro
builds one per call) are made on every call of the code that uses them. The installer used to
hold itself through its closure (to name itself in its errors and to answer itself as a former
factory), so every installer was a reference cycle: in the agora-kanban service ~850 ``on-raise``
calls in 111 seconds left ~26,700 objects waiting for the free-threaded collector, which only runs
when the tracked objects grow by a quarter and stops every thread for ~0.3-2 s.
"""

import gc
from collections.abc import Callable, Generator, Iterator

import pytest
from doeff_core_effects._outcomes import RaiseCase, on_raise

from doeff import do
from doeff.program import handler
from doeff.result import Nothing


class _Refused:
    """A business failure type for the ``on_raise`` case (Python exceptions are refused there)."""


@do
def _pass_body(effect: object, k: object) -> Generator[object, object, object]:
    yield None
    return None


def _local_handler() -> object:
    @do
    def body(effect: object, k: object) -> Generator[object, object, object]:
        yield None
        return None

    return handler(body)


def _on_raise() -> object:
    return on_raise(RaiseCase((_Refused,), lambda reason: Nothing))


@pytest.fixture
def collector_paused() -> Iterator[None]:
    """Stop the automatic collection so that what a build leaves is still there to count."""
    enabled = gc.isenabled()
    gc.disable()
    gc.collect()
    try:
        yield
    finally:
        if enabled:
            gc.enable()


def _cyclic_garbage_of(build: Callable[[], object], times: int) -> int:
    """The objects the cycle collector finds after ``times`` builds (one build first, outside the
    count, for what the first call caches)."""
    build()
    gc.collect()
    for _ in range(times):
        build()
    return gc.collect()


@pytest.mark.parametrize(
    "build",
    [lambda: handler(_pass_body), _local_handler, _on_raise],
    ids=["handler-of-a-do-function", "handler-of-a-local-do-function", "on-raise"],
)
def test_a_handler_built_per_call_leaves_no_cyclic_garbage(
    collector_paused: None, build: Callable[[], object]
) -> None:
    assert _cyclic_garbage_of(build, 200) == 0


def test_a_former_factory_installer_still_answers_itself() -> None:
    from doeff.program import _install_raw

    installer = _install_raw(_pass_body, "test", former_factory=True)
    with pytest.warns(DeprecationWarning):
        assert installer() is installer


@pytest.mark.parametrize("former_factory", [False, True], ids=["handler", "former-factory"])
def test_an_installer_is_carried_by_cloudpickle(former_factory: bool) -> None:
    """A detached task carries its handlers with cloudpickle: the installer's closure holds
    only picklable values (a weak reference to itself could not be pickled)."""
    import cloudpickle

    from doeff.program import _install_raw

    installer = _install_raw(_pass_body, "test", former_factory=former_factory)
    carried = cloudpickle.loads(cloudpickle.dumps(installer))
    assert carried.__name__ == installer.__name__
