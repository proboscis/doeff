"""Regression tests for pinjected bridge Intercept -> WithIntercept migration."""


import ast
import importlib
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from doeff import Annotate, Ask, Step, WithHandler, default_handlers, do, run

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src"
SOURCE_ROOT = PACKAGE_ROOT / "doeff_pinjected"

if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))


class _FakeResolver:
    """Resolver stub that records provide calls."""

    def __init__(self, bindings: dict[str, Any]) -> None:
        self.bindings = dict(bindings)
        self.calls: list[str] = []

    def provide(self, key: str) -> Any:
        self.calls.append(key)
        if key not in self.bindings:
            raise KeyError(key)
        return self.bindings[key]


def _install_pinjected_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a minimal in-memory pinjected module for bridge imports."""
    stub_module = types.ModuleType("pinjected")

    class AsyncResolver:  # pragma: no cover - type shim only
        pass

    class Injected:
        @staticmethod
        def bind(*_args: Any, **_kwargs: Any) -> Any:
            return None

        @staticmethod
        def by_name(name: str) -> str:
            return name

    class IProxy:  # pragma: no cover - type shim only
        pass

    stub_module.AsyncResolver = AsyncResolver
    stub_module.Injected = Injected
    stub_module.IProxy = IProxy
    monkeypatch.setitem(sys.modules, "pinjected", stub_module)


def _load_bridge_module(monkeypatch: pytest.MonkeyPatch):
    _install_pinjected_stub(monkeypatch)
    monkeypatch.delitem(sys.modules, "doeff_pinjected.bridge", raising=False)
    return importlib.import_module("doeff_pinjected.bridge")


def _run_wrapped_program(program: Any, resolver: _FakeResolver):
    from doeff_pinjected.handlers import production_handlers

    return run(
        WithHandler(
            production_handlers(resolver=resolver),
            program,
        ),
        handlers=default_handlers(),
    )


def test_bridge_fallback_resolves_ask_effect(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = _load_bridge_module(monkeypatch)
    resolver = _FakeResolver({"service": "service-instance"})

    @do
    def workflow():
        return (yield Ask("service"))

    monkeypatch.setattr(bridge, "_supports_program_intercept", lambda _prog: False)
    wrapped_program = bridge._program_with_dependency_interception(workflow(), resolver)
    result = _run_wrapped_program(wrapped_program, resolver)

    assert result.is_ok()
    assert result.value == "service-instance"
    assert resolver.calls == ["service"]


def test_bridge_fallback_suppresses_graph_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = _load_bridge_module(monkeypatch)
    resolver = _FakeResolver({"service": "service-instance"})

    @do
    def workflow():
        resolved = yield Ask("service")
        stepped = yield Step(resolved, meta={"phase": "bridge"})
        _ = yield Annotate({"suppressed": True})
        return resolved, stepped

    monkeypatch.setattr(bridge, "_supports_program_intercept", lambda _prog: False)
    wrapped_program = bridge._program_with_dependency_interception(workflow(), resolver)
    result = _run_wrapped_program(wrapped_program, resolver)

    assert result.is_ok()
    assert result.value == ("service-instance", None)
    assert resolver.calls == ["service"]


def test_doeff_pinjected_source_has_no_intercept_imports_or_calls() -> None:
    violations: list[str] = []

    for source_path in sorted(SOURCE_ROOT.rglob("*.py")):
        source = source_path.read_text(encoding="utf-8")
        syntax_tree = ast.parse(source, filename=str(source_path))
        relative = source_path.relative_to(PACKAGE_ROOT.parent)

        for node in ast.walk(syntax_tree):
            if isinstance(node, ast.ImportFrom) and node.module == "doeff.effects":
                for alias in node.names:
                    if alias.name == "Intercept":
                        violations.append(f"{relative}: import Intercept")

            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Intercept":
                violations.append(f"{relative}: call Intercept(...)")

    assert violations == []
