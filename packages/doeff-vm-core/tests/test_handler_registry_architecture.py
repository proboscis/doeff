from pathlib import Path


CORE_ROOT = Path(__file__).resolve().parents[1]
VM_RS = CORE_ROOT / "src" / "vm.rs"
VM_DISPATCH_RS = CORE_ROOT / "src" / "vm" / "dispatch.rs"
VM_STEP_RS = CORE_ROOT / "src" / "vm" / "step.rs"
VM_HANDLER_REGISTRY_RS = CORE_ROOT / "src" / "vm" / "handler_registry.rs"


def _runtime_source(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    return source.split("#[cfg(test)]", 1)[0]


def test_vm_runtime_extracts_handler_registry_module() -> None:
    source = VM_RS.read_text(encoding="utf-8")

    assert '#[path = "vm/handler_registry.rs"]' in source
    assert VM_HANDLER_REGISTRY_RS.exists(), "handler registry should live in src/vm/handler_registry.rs"


def test_handler_registry_owns_installation_lookup_and_continuation_helpers() -> None:
    source = _runtime_source(VM_HANDLER_REGISTRY_RS)

    expected = (
        "fn track_run_handler",
        "fn prepare_with_handler",
        "fn handlers_in_caller_chain",
        "fn should_invoke_handler",
        "fn select_handler",
        "fn register_continuation",
        "fn instantiate_installed_handlers",
    )
    for needle in expected:
        assert needle in source, f"{needle} should be defined in handler_registry.rs"


def test_dispatch_and_step_modules_delegate_handler_registry_logic() -> None:
    dispatch_source = _runtime_source(VM_DISPATCH_RS)
    step_source = _runtime_source(VM_STEP_RS)

    for needle in (
        "fn track_run_handler",
        "fn prepare_with_handler",
        "fn handlers_in_caller_chain",
        "fn instantiate_installed_handlers",
        "fn register_continuation",
        "fn should_invoke_handler",
    ):
        assert needle not in dispatch_source
        assert needle not in step_source

    assert "self.select_handler(" in dispatch_source, (
        "dispatch should ask handler_registry to choose the handler instead of owning "
        "selection loops inline."
    )
    assert "let mut first_type_filtered_skip" not in dispatch_source, (
        "typed handler skip/bootstrap logic should move behind the handler registry selection API."
    )
