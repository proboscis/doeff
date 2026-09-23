"""The Python and Rust schedulers are interchangeable (docs/25-rust-scheduler.md).

- ``scheduled(..., implementation=...)`` / ``$DOEFF_SCHEDULER`` choose the
  implementation; the argument wins over the environment.
- The points downstream code relies on hold for both: a subclass of a
  scheduler effect is handled like its base when nothing intercepts it, a
  handler placed inside ``scheduled`` sees the effect first and may forward it
  with ``Pass``, the scheduler prompt found on a captured handler stack carries
  ``__doeff_scheduler_prompt__`` and can be reinstalled, and a handler that
  itself performs Spawn / Wait / promise effects (a virtual clock) cooperates
  with the scheduler.
"""

from __future__ import annotations

from typing import Any

import pytest
from doeff_core_effects.scheduler import (
    PRIORITY_IDLE,
    AcquireSemaphore,
    CompletePromise,
    CreatePromise,
    CreateSemaphore,
    ReleaseSemaphore,
    SchedulerImplementation,
    Semaphore,
    Spawn,
    Wait,
    resolve_implementation,
    scheduled,
)

from doeff import EffectBase, Pass, Resume, do, run
from doeff import handler as install_handler
from doeff.program import GetOuterHandlers

IMPLEMENTATIONS: list[SchedulerImplementation] = ["python", "rust"]


@do
def _one():
    return 1
    yield  # a generator: @do takes a generator function


def _root_prompt_type(program: Any) -> str:
    return type(program.handler).__name__


def test_argument_wins_over_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOEFF_SCHEDULER", "python")
    assert resolve_implementation() == "python"
    assert resolve_implementation("rust") == "rust"
    assert _root_prompt_type(scheduled(_one(), implementation="rust")) == ("SchedulerPrompt")
    monkeypatch.setenv("DOEFF_SCHEDULER", "rust")
    assert resolve_implementation() == "rust"
    assert _root_prompt_type(scheduled(_one(), implementation="python")) != ("SchedulerPrompt")


def test_unknown_implementation_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOEFF_SCHEDULER", "go")
    with pytest.raises(ValueError, match="'python' or 'rust'"):
        scheduled(_one())


class NamedCreateSemaphore(CreateSemaphore):
    """A downstream subclass (like agora's CreateNamedSemaphore)."""

    def __init__(self, name: str, permits: int = 1) -> None:
        super().__init__(permits)
        self.name = name


class NamedSemaphore(Semaphore):
    def __init__(self, name: str) -> None:
        super().__init__(sem_id=None)
        self.name = name


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_subclass_effect_is_handled_like_its_base(implementation: SchedulerImplementation) -> None:
    @do
    def body():
        sem = yield NamedCreateSemaphore("jobs", 1)
        yield AcquireSemaphore(sem)
        yield ReleaseSemaphore(sem)
        return type(sem).__name__

    assert run(scheduled(body(), implementation=implementation)) == "Semaphore"


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_inner_handler_intercepts_and_forwards(implementation: SchedulerImplementation) -> None:
    seen: list[str] = []

    @do
    def named(effect: CreateSemaphore, k: Any):
        if isinstance(effect, NamedCreateSemaphore):
            seen.append(effect.name)
            return (yield Resume(k, NamedSemaphore(effect.name)))
        yield Pass(effect, k)

    @do
    def body():
        named_sem = yield NamedCreateSemaphore("cluster", 1)
        local = yield CreateSemaphore(2)
        yield AcquireSemaphore(local)
        yield ReleaseSemaphore(local)
        return type(named_sem).__name__, type(local).__name__

    program = scheduled(install_handler(named)(body()), implementation=implementation)
    assert run(program) == ("NamedSemaphore", "Semaphore")
    assert seen == ["cluster"]


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_captured_scheduler_prompt_is_marked_and_reinstallable(
    implementation: SchedulerImplementation,
) -> None:
    @do
    def child(x: int):
        return x * 2
        yield  # a generator: @do takes a generator function

    @do
    def capture(effect: Ping, k: Any):
        # The handlers installed above this one (the scheduler among them).
        handlers = yield GetOuterHandlers()
        return (yield Resume(k, handlers))

    @do
    def body():
        handlers = yield Ping()
        prompts = [h for h in handlers if getattr(h, "__doeff_scheduler_prompt__", False)]
        assert len(prompts) == 1
        # Reinstall the captured prompt around a spawning program: the
        # innermost scheduler prompt handles its Spawn / Wait.
        inner = install_handler(prompts[0])(_spawn_and_wait(child(21)))
        return (yield inner)

    program = scheduled(install_handler(capture)(body()), implementation=implementation)
    assert run(program) == 42


class Ping(EffectBase[list[Any]]):
    pass


@do
def _spawn_and_wait(program: Any):
    task = yield Spawn(program)
    return (yield Wait(task))


class Sleep(EffectBase[None]):
    def __init__(self, ticks: int) -> None:
        super().__init__()
        self.ticks = ticks


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_virtual_clock_handler_cooperates_with_the_scheduler(
    implementation: SchedulerImplementation,
) -> None:
    clock: dict[str, Any] = {"now": 0, "queue": [], "running": False}

    @do
    def driver():
        try:
            while clock["queue"]:
                clock["queue"].sort(key=lambda item: item[0])
                at, promise = clock["queue"].pop(0)
                clock["now"] = at
                yield CompletePromise(promise, None)
        finally:
            clock["running"] = False

    @do
    def sim_clock(effect: Sleep, k: Any):
        promise = yield CreatePromise()
        clock["queue"].append((clock["now"] + effect.ticks, promise))
        if not clock["running"]:
            clock["running"] = True
            yield Spawn(driver(), priority=PRIORITY_IDLE, daemon=True)
        yield Wait(promise.future)
        return (yield Resume(k, None))

    order: list[tuple[str, int]] = []

    @do
    def sleeper(name: str, ticks: int):
        for _ in range(2):
            yield Sleep(ticks)
            order.append((name, clock["now"]))
        return name

    @do
    def body():
        a = yield Spawn(sleeper("a", 3))
        b = yield Spawn(sleeper("b", 2))
        return [(yield Wait(a)), (yield Wait(b))]

    program = scheduled(install_handler(sim_clock)(body()), implementation=implementation)
    assert run(program) == ["a", "b"]
    assert order == [("b", 2), ("a", 3), ("b", 4), ("a", 6)]


def test_default_is_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rust becomes the default only after every deployment rebuilt the extension."""
    monkeypatch.delenv("DOEFF_SCHEDULER", raising=False)
    assert resolve_implementation() == "python"


def test_stale_extension_is_named_not_silently_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import doeff_vm.doeff_vm as extension

    monkeypatch.delattr(extension, "SchedulerCore")
    with pytest.raises(ImportError, match="rebuild the Rust extension"):
        scheduled(_one(), implementation="rust")
