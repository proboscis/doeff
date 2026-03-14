from pathlib import Path
import re


CORE_ROOT = Path(__file__).resolve().parents[1]
KLEISLI_RS = CORE_ROOT / "src/kleisli.rs"
HANDLER_RS = CORE_ROOT / "src/handler.rs"
VM_DISPATCH_RS = CORE_ROOT / "src/vm/dispatch.rs"
VM_STEP_RS = CORE_ROOT / "src/vm/step.rs"
VM_TRACE_RS = CORE_ROOT / "src/vm/vm_trace.rs"


def _runtime_source(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    return source.split("#[cfg(test)]", 1)[0]


def _block(source: str, token: str) -> str:
    start = source.find(token)
    assert start != -1, f"missing block token: {token}"

    brace = source.find("{", start)
    assert brace != -1, f"missing opening brace for block: {token}"

    depth = 0
    for index in range(brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]

    raise AssertionError(f"unterminated block: {token}")


def test_kleisli_apply_signatures_do_not_require_python_at_call_site() -> None:
    source = _runtime_source(KLEISLI_RS)
    block = _block(source, "pub trait Kleisli")

    assert "fn apply(&self, py: Python<'_>, args: Vec<Value>)" not in block
    assert "py: Python<'_>" not in block
    assert re.search(r"fn apply\(&self,\s*args: Vec<Value>\)", block)
    assert re.search(
        r"fn apply_with_run_token\(\s*&self,\s*args: Vec<Value>,\s*run_token: Option<u64>",
        block,
    )


def test_ir_stream_program_start_no_longer_requires_python_at_call_site() -> None:
    source = _runtime_source(HANDLER_RS)
    block = _block(source, "pub trait IRStreamProgram")

    assert "_py: Python<'_>" not in block
    assert "py: Python<'_>" not in block
    assert re.search(
        r"fn start\(\s*&mut self,\s*effect: DispatchEffect,\s*k: Continuation,",
        block,
    )


def test_step_kleisli_calls_do_not_force_python_attach() -> None:
    source = _runtime_source(VM_STEP_RS)

    assert "Python::attach(|py| kleisli.apply_with_run_token(py, args_values, run_token))" not in source
    assert source.count("kleisli.apply_with_run_token(args_values, run_token)") >= 2


def test_rust_kleisli_stream_does_not_wrap_program_callbacks_in_python_attach() -> None:
    source = _runtime_source(KLEISLI_RS)
    block = _block(source, "impl IRStream for RustKleisliStream")

    assert "Python::attach(|py|" not in block
    assert "Python::attach(|_py|" not in block


def test_dispatch_reuses_materialized_effect_object_for_handler_invocation() -> None:
    dispatch_source = _runtime_source(VM_DISPATCH_RS)
    start_dispatch = _block(
        dispatch_source,
        "pub fn start_dispatch(&mut self, effect: DispatchEffect) -> Result<StepEvent, VMError>",
    )

    assert re.search(r"invoke_kleisli_handler_expr\(\s*handler,\s*effect_obj,", start_dispatch)
    assert not re.search(
        r"let\s+effect_obj\s*=\s*Python::attach\(\|py\|\s*dispatch_to_pyobject\(py,\s*&effect\)",
        start_dispatch,
    )


def test_handler_expr_builder_does_not_reconvert_dispatch_effect() -> None:
    source = _runtime_source(VM_TRACE_RS)
    block = _block(source, "pub(super) fn invoke_kleisli_handler_expr(")

    assert "dispatch_to_pyobject" not in block
