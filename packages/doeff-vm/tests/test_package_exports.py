from __future__ import annotations

import importlib


def test_package_exports_runtime_api_symbols() -> None:
    mod = importlib.import_module("doeff_vm")
    required = (
        "run",
        "async_run",
        "state",
        "reader",
        "writer",
        "RunResult",
        "PyVM",
    )
    missing = [name for name in required if not hasattr(mod, name)]
    assert not missing, f"missing module exports: {missing}"


def test_package_all_contains_runtime_contract() -> None:
    mod = importlib.import_module("doeff_vm")
    exported = set(getattr(mod, "__all__", []))
    expected = {
        "run",
        "async_run",
        "state",
        "reader",
        "writer",
        "RunResult",
        "PyVM",
        "RustHandler",
    }
    assert expected.issubset(exported)


def test_submodule_and_package_share_runtime_symbols() -> None:
    pkg = importlib.import_module("doeff_vm")
    sub = importlib.import_module("doeff_vm.doeff_vm")
    assert pkg.run is sub.run
    assert pkg.async_run is sub.async_run
    assert pkg.state is sub.state
    assert pkg.reader is sub.reader
    assert pkg.writer is sub.writer
