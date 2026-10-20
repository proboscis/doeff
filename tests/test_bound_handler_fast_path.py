"""@do の bound method でも VM が定義済みの generator を直接実行する (#1384)。"""

from collections.abc import Generator
from types import MethodType
from typing import NamedTuple

import pytest
from doeff_vm import K, WithHandler
from doeff_vm._effect_types import handler_spec

from doeff import EffectBase, Pure, Resume, do, handler, run
from doeff.traceback import format_default


class ReadNumber(EffectBase[int]):
    pass


class OtherNumber(EffectBase[int]):
    pass


class Answers:
    def __init__(self, number: int) -> None:
        self.number = number

    @do
    def read(self, effect: ReadNumber, k: K) -> Generator[Resume, int, int]:
        assert isinstance(effect, ReadNumber)
        if self.number < 0:
            raise ValueError("negative number")
        return (yield Resume(k, self.number))

    def plain(self, effect: ReadNumber, k: K) -> Resume:
        assert isinstance(effect, ReadNumber)
        return Resume(k, self.number)

    @do
    def translate(
        self, effect: ReadNumber, k: K
    ) -> Generator[OtherNumber | Resume, int, int]:
        assert isinstance(effect, ReadNumber)
        other: int = yield OtherNumber()
        return (yield Resume(k, self.number + other))

    @classmethod
    @do
    def class_read(cls, effect: ReadNumber, k: K) -> Generator[Resume, int, int]:
        assert cls is Answers
        assert isinstance(effect, ReadNumber)
        return (yield Resume(k, 19))


def test_bound_do_handler_uses_its_bound_generator_definition() -> None:
    first: Answers = Answers(7)
    second: Answers = Answers(11)
    for owner in (first, second):
        spec = handler_spec(owner.read)
        assert isinstance(spec.generator_function, MethodType)
        assert spec.generator_function.__self__ is owner
        assert spec.generator_function.__func__ is Answers.read.__dict__["__wrapped__"]
        assert spec.effect_types == (ReadNumber,)
        assert spec.tail_resume_lines == Answers.read.__dict__["__doeff_tail_resume_lines__"]
        assert run(handler(owner.read)(ReadNumber())) == owner.number


def test_bound_classmethod_keeps_class_as_receiver() -> None:
    spec = handler_spec(Answers.class_read)
    assert isinstance(spec.generator_function, MethodType)
    assert spec.generator_function.__self__ is Answers
    assert run(handler(Answers.class_read)(ReadNumber())) == 19


def test_ordinary_bound_method_is_not_a_handler() -> None:
    # A handler is a def under @handler, or a @do function (ADR-DOE-CORE-003): a bound method of a
    # plain method is neither, and is refused where it is made a handler. The VM still reads it as
    # a raw dispatcher (no generator to run).
    owner: Answers = Answers(23)
    assert handler_spec(owner.plain).generator_function is None
    with pytest.raises(TypeError, match=r"Answers\.plain is not a handler body"):
        handler(owner.plain)


def test_bound_handler_filter_still_passes_unrelated_effects() -> None:
    @handler
    def outer(effect: OtherNumber, k: K) -> Resume:
        assert isinstance(effect, OtherNumber)
        return (yield Resume(k, 31))

    owner: Answers = Answers(7)
    assert run(handler(outer)(handler(owner.read)(OtherNumber()))) == 31
    assert run(handler(owner.read)(Pure(41))) == 41


def test_bound_handler_exception_is_preserved() -> None:
    owner: Answers = Answers(-1)
    with pytest.raises(ValueError, match="negative number"):
        run(handler(owner.read)(ReadNumber()))


class PathOutcome(NamedTuple):
    value: object
    unrelated: object
    emitted: object
    seen: tuple[str, ...]
    exception: type[Exception]
    traceback: str


def compare_path(legacy: bool, monkeypatch: pytest.MonkeyPatch) -> PathOutcome:
    with monkeypatch.context() as patch:
        if legacy:
            # @do の Program を作る wrapper 自体は変えず、VM の直接実行
            # の印だけ外し、変更前と同じ経路で同じ定義を動かす。
            patch.delitem(Answers.read.__dict__, "__doeff_generator_function__")
            patch.delitem(Answers.translate.__dict__, "__doeff_generator_function__")

        seen: list[str] = []

        @handler
        def outer(effect: OtherNumber, k: K) -> Resume:
            assert isinstance(effect, OtherNumber)
            seen.append("outer")
            return (yield Resume(k, 31))

        def install(bound: object) -> object:
            """The installer of a bound @do method. Without the direct-run mark the method is no
            @do function to handler() (ADR-DOE-CORE-003), so the legacy path installs it on the VM
            node itself — the route handler() took before the mark existed."""
            if legacy:
                return lambda body: WithHandler(bound, body)
            return handler(bound)

        owner: Answers = Answers(7)
        value = run(install(owner.read)(ReadNumber()))
        unrelated = run(handler(outer)(install(owner.read)(OtherNumber())))
        emitted = run(handler(outer)(install(owner.translate)(ReadNumber())))
        broken: Answers = Answers(-1)
        with pytest.raises(ValueError, match="negative number") as raised:
            run(install(broken.read)(ReadNumber()))
        rendered = format_default(raised.value)
        assert isinstance(rendered, str)
        assert "read()" in rendered
        assert "ValueError: negative number" in rendered
        return PathOutcome(value, unrelated, emitted, tuple(seen), type(raised.value), rendered)


def test_direct_and_program_paths_have_identical_results_and_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = compare_path(True, monkeypatch)
    direct = compare_path(False, monkeypatch)
    assert legacy == direct
    assert direct[:4] == (7, 31, 38, ("outer", "outer"))


def test_rewrapping_an_installed_handler_returns_it_without_walking_the_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    """doeff-traverse re-wraps every inner handler per item — the installed marker is read as an attribute, not by an
    ``isinstance`` against the runtime-checkable Protocol (its member walk via ``inspect.getattr_static`` — agora-redesign
    #2593: 181k such checks in one screen test)."""
    import typing

    installed = handler(Answers(7).read)
    checked: list[type] = []
    original = typing._ProtocolMeta.__instancecheck__  # pyright: ignore[reportAttributeAccessIssue] - the private metaclass hook is what this test counts

    def counting(cls: type, instance: object) -> bool:
        checked.append(cls)
        return original(cls, instance)

    monkeypatch.setattr(typing._ProtocolMeta, "__instancecheck__", counting)  # pyright: ignore[reportAttributeAccessIssue] - same private hook
    assert handler(installed) is installed
    assert checked == []
