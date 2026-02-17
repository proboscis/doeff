from __future__ import annotations

from types import SimpleNamespace

import pytest
import doeff_vm

from doeff import Program
from doeff import rust_vm as rust_vm_module


def test_default_handlers_requires_module_sentinels(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_vm = SimpleNamespace()
    monkeypatch.setattr(rust_vm_module, "_vm", lambda: fake_vm)

    with pytest.raises(RuntimeError, match="missing required handler sentinels"):
        rust_vm_module.default_handlers()


def test_default_handlers_are_module_sentinels_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from doeff.effects.future import sync_await_handler

    sentinels = {
        "state": object(),
        "reader": object(),
        "writer": object(),
        "result_safe": object(),
        "scheduler": object(),
        "lazy_ask": object(),
    }
    fake_vm = SimpleNamespace(**sentinels)
    monkeypatch.setattr(rust_vm_module, "_vm", lambda: fake_vm)

    handlers = rust_vm_module.default_handlers()

    assert handlers == [
        sentinels["state"],
        sentinels["reader"],
        sentinels["writer"],
        sentinels["result_safe"],
        sentinels["scheduler"],
        sentinels["lazy_ask"],
        sync_await_handler,
    ]


def test_default_async_handlers_use_async_await_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from doeff.effects.future import async_await_handler

    sentinels = {
        "state": object(),
        "reader": object(),
        "writer": object(),
        "result_safe": object(),
        "scheduler": object(),
        "lazy_ask": object(),
    }
    fake_vm = SimpleNamespace(**sentinels)
    monkeypatch.setattr(rust_vm_module, "_vm", lambda: fake_vm)

    handlers = rust_vm_module.default_async_handlers()

    assert handlers == [
        sentinels["state"],
        sentinels["reader"],
        sentinels["writer"],
        sentinels["result_safe"],
        sentinels["scheduler"],
        sentinels["lazy_ask"],
        async_await_handler,
    ]


def test_run_requires_module_level_run(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_vm = SimpleNamespace(state=object(), reader=object(), writer=object())
    monkeypatch.setattr(rust_vm_module, "_vm", lambda: fake_vm)

    with pytest.raises(RuntimeError, match="does not expose run"):
        rust_vm_module.run(Program.pure(1), handlers=[])


@pytest.mark.asyncio
async def test_async_run_requires_module_level_async_run(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_vm = SimpleNamespace(
        run=lambda *args, **kwargs: None, state=object(), reader=object(), writer=object()
    )
    monkeypatch.setattr(rust_vm_module, "_vm", lambda: fake_vm)

    with pytest.raises(RuntimeError, match="does not expose async_run"):
        await rust_vm_module.async_run(Program.pure(1), handlers=[])


def test_run_normalizes_top_level_expr(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(
        program: object,
        *,
        handlers: list[object],
        env: dict[str, object] | None,
        store: dict[str, object] | None,
    ) -> str:
        captured["program"] = program
        captured["handlers"] = handlers
        captured["env"] = env
        captured["store"] = store
        return "ok"

    fake_vm = SimpleNamespace(
        run=fake_run,
        state=object(),
        reader=object(),
        writer=object(),
        EffectBase=doeff_vm.EffectBase,
        DoExpr=doeff_vm.DoExpr,
        Perform=doeff_vm.Perform,
    )
    monkeypatch.setattr(rust_vm_module, "_vm", lambda: fake_vm)

    result = rust_vm_module.run(Program.pure(2), handlers=[])
    assert result == "ok"
    assert isinstance(captured["program"], doeff_vm.DoExpr)


def test_run_rejects_non_program_object(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"run": False}

    def fake_run(*args: object, **kwargs: object) -> str:
        called["run"] = True
        return "ok"

    fake_vm = SimpleNamespace(
        run=fake_run,
        state=object(),
        reader=object(),
        writer=object(),
        EffectBase=doeff_vm.EffectBase,
        DoExpr=doeff_vm.DoExpr,
        Perform=doeff_vm.Perform,
    )
    monkeypatch.setattr(rust_vm_module, "_vm", lambda: fake_vm)

    with pytest.raises(TypeError, match=r"requires DoExpr\[T\] or EffectValue\[T\]"):
        rust_vm_module.run(object(), handlers=[])

    assert called["run"] is False
