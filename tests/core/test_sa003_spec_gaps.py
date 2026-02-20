"""Failing tests for SA-003 spec-gap audit (Phase 4).

These tests are structural/behavioral guards for SA-003 code-resolution items.
"""

from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
RUST_SRC = ROOT / "packages" / "doeff-vm" / "src"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_fn_body(source: str, fn_name: str) -> str:
    m = re.search(rf"fn\s+{re.escape(fn_name)}\s*\(", source)
    assert m, f"function not found: {fn_name}"
    start = m.start()
    brace = source.find("{", start)
    assert brace != -1, f"function body start not found: {fn_name}"
    depth = 0
    for i in range(brace, len(source)):
        ch = source[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[brace : i + 1]
    raise AssertionError(f"function body end not found: {fn_name}")


def test_SA_003_G01_callfunc_path_has_distinct_pending_state() -> None:
    src = _read(RUST_SRC / "vm.rs")
    body = _extract_fn_body(src, "step_handle_yield")
    assert "PendingPython::CallFuncReturn" in body
    assert "PendingPython::ExpandReturn" in body
    assert "DoCtrl::Apply" in body
    assert "DoCtrl::Expand" in body


def test_SA_003_G02_python_call_errors_are_normalized_to_generror() -> None:
    src = _read(RUST_SRC / "pyvm.rs")
    body = _extract_fn_body(src, "execute_python_call")

    assert "PythonCall::StartProgram" in body
    assert "to_generator_strict(py, program.clone_ref(py))?" not in body
    assert "PythonCall::CallHandler" in body
    assert "to_generator_strict(py, result.unbind())?" not in body
    assert "PythonCall::CallAsync" in body
    assert "Ok(PyCallOutcome::GenError" in body


def test_SA_003_G03_isolated_spawn_without_snapshot_must_throw() -> None:
    src = _read(RUST_SRC / "scheduler.rs")
    assert "None => TaskStore::Shared" not in src


def test_SA_003_G04_get_handlers_must_not_skip_missing_entries() -> None:
    src = _read(RUST_SRC / "vm.rs")
    body = _extract_fn_body(src, "handle_get_handlers")
    assert "filter_map" not in body


def test_SA_003_G06_python_async_syntax_escape_alias_exported() -> None:
    py_ext = _read(ROOT / "packages" / "doeff-vm" / "doeff_vm" / "__init__.py")
    py_wrap = _read(ROOT / "doeff" / "rust_vm.py")
    assert "PythonAsyncSyntaxEscape" in py_ext
    assert "PythonAsyncSyntaxEscape" in py_wrap


def test_SA_003_G07_safe_has_no_python_kernel_proxy() -> None:
    src = _read(ROOT / "doeff" / "effects" / "result.py")
    assert "_wrap_kernel_as_result" not in src
    assert "_clone_kpc_with_kernel" not in src
    assert "ResultSafeEffect(" in src


def test_SA_003_G08_result_safe_handler_is_registered() -> None:
    handler_src = _read(RUST_SRC / "handler.rs")
    pyvm_src = _read(RUST_SRC / "pyvm.rs")
    rust_vm_src = _read(ROOT / "doeff" / "rust_vm.py")

    assert "ResultSafeHandlerFactory" in handler_src
    assert '"result_safe"' in pyvm_src
    assert '"result_safe"' in rust_vm_src
