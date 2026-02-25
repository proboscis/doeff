from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import doeff_vm

from doeff import EffectBase, default_handlers, do, run


def _with_handlers(program: Any, *handlers: Any) -> Any:
    wrapped = program
    for handler in handlers:
        wrapped = doeff_vm.WithHandler(handler, wrapped)
    return wrapped


def _is_ok(result: Any) -> bool:
    probe = getattr(result, "is_ok", None)
    if callable(probe):
        return bool(probe())
    return bool(probe)


def _cont_id_from_repr(text: str) -> int:
    # K repr is currently "K(<cont_id>)"
    return int(text.removeprefix("K(").removesuffix(")"))


@dataclass(frozen=True, kw_only=True)
class ModeOuter(EffectBase):
    tag: str = "mode-outer"


@dataclass(frozen=True, kw_only=True)
class ModeInner(EffectBase):
    tag: str = "mode-inner"


@dataclass(frozen=True, kw_only=True)
class ModeTail(EffectBase):
    tag: str = "mode-tail"


def test_mode_preserved_across_nested_dispatch() -> None:
    seen: list[str] = []

    def outer_handler(effect: object, k: object):
        if not isinstance(effect, ModeOuter):
            yield doeff_vm.Pass()
            return
        seen.append("outer:start")
        inner = yield ModeInner()
        seen.append(f"outer:after-inner:{inner}")
        tail = yield ModeTail()
        seen.append(f"outer:after-tail:{tail}")
        return (yield doeff_vm.Resume(k, f"{inner}|{tail}"))

    def inner_handler(effect: object, k: object):
        if not isinstance(effect, ModeInner):
            yield doeff_vm.Pass()
            return
        return (yield doeff_vm.Resume(k, "inner-ok"))

    def tail_handler(effect: object, k: object):
        if not isinstance(effect, ModeTail):
            yield doeff_vm.Pass()
            return
        return (yield doeff_vm.Resume(k, "tail-ok"))

    @do
    def program():
        return (yield ModeOuter())

    wrapped = _with_handlers(program(), outer_handler, inner_handler, tail_handler)
    result = run(wrapped, handlers=default_handlers())
    assert _is_ok(result), result.error
    assert seen == [
        "outer:start",
        "outer:after-inner:inner-ok",
        "outer:after-tail:tail-ok",
    ]


@dataclass(frozen=True, kw_only=True)
class SegmentOuter(EffectBase):
    tag: str = "segment-outer"


@dataclass(frozen=True, kw_only=True)
class SegmentInner(EffectBase):
    tag: str = "segment-inner"


@dataclass(frozen=True, kw_only=True)
class SegmentProbe(EffectBase):
    tag: str = "segment-probe"


def test_current_segment_preserved_across_nested_dispatch() -> None:
    seen: list[str] = []

    def outer_handler(effect: object, k: object):
        if not isinstance(effect, SegmentOuter):
            yield doeff_vm.Pass()
            return
        _ = yield doeff_vm.WithHandler(inner_temporary_handler, SegmentInner())
        probe = yield SegmentProbe()
        seen.append(probe)
        return (yield doeff_vm.Resume(k, probe))

    def inner_temporary_handler(effect: object, k: object):
        if isinstance(effect, SegmentInner):
            return (yield doeff_vm.Resume(k, "inner-ok"))
        if isinstance(effect, SegmentProbe):
            return (yield doeff_vm.Resume(k, "inner-segment-leak"))
        yield doeff_vm.Pass()

    def outer_probe_handler(effect: object, k: object):
        if not isinstance(effect, SegmentProbe):
            yield doeff_vm.Pass()
            return
        return (yield doeff_vm.Resume(k, "outer-segment-ok"))

    @do
    def program():
        return (yield SegmentOuter())

    wrapped = _with_handlers(program(), outer_handler, outer_probe_handler)
    result = run(wrapped, handlers=default_handlers())
    assert _is_ok(result), result.error
    assert seen == ["outer-segment-ok"]


@dataclass(frozen=True, kw_only=True)
class ScopeOuter(EffectBase):
    tag: str = "scope-outer"


@dataclass(frozen=True, kw_only=True)
class ScopeInner(EffectBase):
    tag: str = "scope-inner"


@dataclass(frozen=True, kw_only=True)
class ScopeProbe(EffectBase):
    tag: str = "scope-probe"


def test_scope_chain_correct_after_nested_dispatch() -> None:
    def outer_handler(effect: object, k: object):
        if not isinstance(effect, ScopeOuter):
            yield doeff_vm.Pass()
            return
        _ = yield doeff_vm.WithHandler(inner_scope_handler, ScopeInner())
        probe = yield doeff_vm.WithHandler(passthrough_scope_handler, ScopeProbe())
        return (yield doeff_vm.Resume(k, probe))

    def inner_scope_handler(effect: object, k: object):
        if isinstance(effect, ScopeInner):
            return (yield doeff_vm.Resume(k, "inner-ok"))
        if isinstance(effect, ScopeProbe):
            return (yield doeff_vm.Resume(k, "inner-scope-leak"))
        yield doeff_vm.Pass()

    def passthrough_scope_handler(effect: object, k: object):
        if isinstance(effect, ScopeProbe):
            delegated = yield doeff_vm.Delegate()
            return (yield doeff_vm.Resume(k, delegated))
        yield doeff_vm.Pass()

    def outer_scope_handler(effect: object, k: object):
        if not isinstance(effect, ScopeProbe):
            yield doeff_vm.Pass()
            return
        return (yield doeff_vm.Resume(k, "outer-scope-ok"))

    @do
    def program():
        return (yield ScopeOuter())

    wrapped = _with_handlers(program(), outer_handler, outer_scope_handler)
    result = run(wrapped, handlers=default_handlers())
    assert _is_ok(result), result.error
    assert result.value == "outer-scope-ok"


@dataclass(frozen=True, kw_only=True)
class ContinuationOuter(EffectBase):
    tag: str = "continuation-outer"


@dataclass(frozen=True, kw_only=True)
class ContinuationInner(EffectBase):
    tag: str = "continuation-inner"


def test_continuation_capture_correct_after_nested_dispatch() -> None:
    observed: list[tuple[str, int, bool, int]] = []

    def outer_handler(effect: object, k: object):
        if not isinstance(effect, ContinuationOuter):
            yield doeff_vm.Pass()
            return
        _ = yield ContinuationInner()
        captured = yield doeff_vm.GetContinuation()
        hops = yield doeff_vm.GetTraceback(k)
        payload = (repr(k), captured["cont_id"], bool(captured["started"]), len(hops))
        observed.append(payload)
        return (yield doeff_vm.Resume(k, payload))

    def inner_handler(effect: object, k: object):
        if not isinstance(effect, ContinuationInner):
            yield doeff_vm.Pass()
            return
        return (yield doeff_vm.Resume(k, "inner-ok"))

    @do
    def program():
        return (yield ContinuationOuter())

    wrapped = _with_handlers(program(), outer_handler, inner_handler)
    result = run(wrapped, handlers=default_handlers())
    assert _is_ok(result), result.error
    assert len(observed) == 1
    outer_repr, captured_cont_id, started, hop_count = observed[0]
    assert captured_cont_id == _cont_id_from_repr(outer_repr)
    assert started
    assert hop_count >= 1


@dataclass(frozen=True, kw_only=True)
class DeepA(EffectBase):
    tag: str = "deep-a"


@dataclass(frozen=True, kw_only=True)
class DeepB(EffectBase):
    tag: str = "deep-b"


@dataclass(frozen=True, kw_only=True)
class DeepC(EffectBase):
    tag: str = "deep-c"


@dataclass(frozen=True, kw_only=True)
class DeepTail(EffectBase):
    tag: str = "deep-tail"


def test_deeply_nested_dispatches_restore_correctly() -> None:
    seen: list[str] = []

    def handler_a(effect: object, k: object):
        if not isinstance(effect, DeepA):
            yield doeff_vm.Pass()
            return
        seen.append("a:start")
        b = yield DeepB()
        seen.append(f"a:after-b:{b}")
        tail = yield DeepTail()
        seen.append(f"a:after-tail:{tail}")
        return (yield doeff_vm.Resume(k, f"A({b}|{tail})"))

    def handler_b(effect: object, k: object):
        if not isinstance(effect, DeepB):
            yield doeff_vm.Pass()
            return
        seen.append("b:start")
        c = yield DeepC()
        seen.append(f"b:after-c:{c}")
        return (yield doeff_vm.Resume(k, f"B({c})"))

    def handler_c(effect: object, k: object):
        if not isinstance(effect, DeepC):
            yield doeff_vm.Pass()
            return
        seen.append("c:start")
        return (yield doeff_vm.Resume(k, "C"))

    def tail_handler(effect: object, k: object):
        if not isinstance(effect, DeepTail):
            yield doeff_vm.Pass()
            return
        return (yield doeff_vm.Resume(k, "tail"))

    @do
    def program():
        return (yield DeepA())

    wrapped = _with_handlers(program(), handler_a, handler_b, handler_c, tail_handler)
    result = run(wrapped, handlers=default_handlers(), trace=True)
    assert _is_ok(result), result.error
    assert seen == [
        "a:start",
        "b:start",
        "c:start",
        "b:after-c:C",
        "a:after-b:B(C)",
        "a:after-tail:tail",
    ]
    trace = list(result.trace)
    assert max(row["dispatch_depth"] for row in trace) >= 3


@dataclass(frozen=True, kw_only=True)
class NeedsPyOuter(EffectBase):
    tag: str = "needs-py-outer"


@dataclass(frozen=True, kw_only=True)
class NeedsPyInner(EffectBase):
    label: str


@dataclass(frozen=True, kw_only=True)
class NeedsPyTail(EffectBase):
    tag: str = "needs-py-tail"


def test_needs_python_round_trip_preserves_state() -> None:
    observed: list[tuple[str, str]] = []

    def outer_handler(effect: object, k: object):
        if not isinstance(effect, NeedsPyOuter):
            yield doeff_vm.Pass()
            return
        inner = yield NeedsPyInner(label="seed")
        tail = yield NeedsPyTail()
        observed.append((inner, tail))
        return (yield doeff_vm.Resume(k, (inner, tail)))

    def inner_handler(effect: object, k: object):
        if not isinstance(effect, NeedsPyInner):
            yield doeff_vm.Pass()
            return
        meta = {
            "function_name": "inner_apply",
            "source_file": __file__,
            "source_line": 1,
        }
        first = yield doeff_vm.Apply(lambda text: f"{text}-py1", [effect.label], {}, meta)
        second = yield doeff_vm.Apply(lambda text: f"{text}-py2", [first], {}, meta)
        return (yield doeff_vm.Resume(k, second))

    def tail_handler(effect: object, k: object):
        if not isinstance(effect, NeedsPyTail):
            yield doeff_vm.Pass()
            return
        return (yield doeff_vm.Resume(k, "tail-ok"))

    @do
    def program():
        return (yield NeedsPyOuter())

    wrapped = _with_handlers(program(), outer_handler, inner_handler, tail_handler)
    result = run(wrapped, handlers=default_handlers(), trace=True)
    assert _is_ok(result), result.error
    assert observed == [("seed-py1-py2", "tail-ok")]

    trace = list(result.trace)
    callfunc_events = [row for row in trace if row.get("result") == "NeedsPython(CallFunc)"]
    assert len(callfunc_events) >= 2
