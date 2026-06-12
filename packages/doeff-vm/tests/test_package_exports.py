"""Package export checks for the current doeff-vm bridge API."""

import importlib

CURRENT_RUNTIME_SYMBOLS = (
    "PyVM",
    "K",
    "Callable",
    "EffectBase",
    "IRStream",
    "UnhandledEffect",
    "Ok",
    "Err",
    "Pure",
    "Perform",
    "Resume",
    "Transfer",
    "Apply",
    "Expand",
    "Pass",
    "WithHandler",
    "ResumeThrow",
    "TransferThrow",
    "WithObserve",
    "GetTraceback",
    "GetExecutionContext",
    "GetHandlers",
    "GetOuterHandlers",
    "TailEval",
    "vm_live_counts",
)

REMOVED_FACADE_SYMBOLS = (
    "run",
    "async_run",
    "state",
    "reader",
    "writer",
    "scheduler",
    "RunResult",
    "DoeffTracebackData",
    "memory_stats",
    "RustHandler",
)


def test_package_exports_current_runtime_api_symbols() -> None:
    mod = importlib.import_module("doeff_vm")

    missing = [name for name in CURRENT_RUNTIME_SYMBOLS if not hasattr(mod, name)]
    assert not missing, f"missing package exports: {missing}"


def test_removed_facade_symbols_are_not_reexported_from_vm_package() -> None:
    mod = importlib.import_module("doeff_vm")

    unexpected = [name for name in REMOVED_FACADE_SYMBOLS if hasattr(mod, name)]
    assert not unexpected, f"removed facade symbols still exported: {unexpected}"


def test_submodule_and_package_share_current_runtime_symbols() -> None:
    package = importlib.import_module("doeff_vm")
    extension = importlib.import_module("doeff_vm.doeff_vm")

    for name in CURRENT_RUNTIME_SYMBOLS:
        assert getattr(package, name) is getattr(extension, name)
