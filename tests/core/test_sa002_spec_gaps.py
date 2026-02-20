"""Failing tests for SA-002 spec-gap audit (Phase 4).

Each test maps to one SA-002 gap and is expected to fail on current code.
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


def test_SA_002_G01_classifier_no_fallback_introspection() -> None:
    src = _read(RUST_SRC / "pyvm.rs")
    body = _extract_fn_body(src, "classify_yielded")
    assert 'py.import("doeff.types")' not in body
    assert "__doeff_effect_base__" not in body
    assert "__doeff_kpc__" not in body
    assert "is_effect_object(" not in body


def test_SA_002_G02_no_python_kpc_transitional_state() -> None:
    py_program = _read(ROOT / "doeff" / "program.py")
    py_types = _read(ROOT / "doeff" / "_types_internal.py")
    rust_effect = _read(RUST_SRC / "effect.rs")

    assert "class " + "Kleisli" + "ProgramCall(" not in py_program
    assert "_KPC.__bases__" not in py_types
    assert "pub struct " + "Py" + "KPC" not in rust_effect


def test_SA_002_G03_no_implicit_kpc_install_in_vm_new() -> None:
    src = _read(RUST_SRC / "pyvm.rs")
    body = _extract_fn_body(src, "new")
    assert "KpcHandlerFactory" not in body
    assert "install_handler" not in body


def test_SA_002_G04_runresult_protocol_has_required_members() -> None:
    src = _read(ROOT / "doeff" / "_types_internal.py")
    block_start = src.find("class RunResult(Protocol")
    assert block_start != -1
    block = src[block_start : block_start + 500]
    assert "def result" in block
    assert "def raw_store" in block or "raw_store" in block
    assert "def error" in block or "error" in block


def test_SA_002_G05_default_handlers_and_presets_contract() -> None:
    from doeff import presets
    from doeff.rust_vm import default_handlers

    handlers = default_handlers()
    names = [str(getattr(h, "name", repr(h))).lower() for h in handlers]
    assert len(handlers) == 7, "default_handlers() must include core runtime handlers"
    assert any("state" in n for n in names)
    assert any("reader" in n for n in names)
    assert any("writer" in n for n in names)
    assert not any("kpc" in n for n in names)
    assert presets.sync_preset != presets.async_preset


def test_SA_002_G06_scheduler_wake_waiters_has_resume_path() -> None:
    src = _read(RUST_SRC / "scheduler.rs")
    body = _extract_fn_body(src, "wake_waiters")
    assert "doesn't need to enqueue" not in body
    assert "re-check try_collect/try_race" not in body


def test_SA_002_G07_doctrl_pyclasses_extend_base() -> None:
    src = _read(RUST_SRC / "pyvm.rs")
    for ctrl in ("PyResume", "PyDelegate", "PyTransfer"):
        m = re.search(rf"#\[pyclass\(([^\)]*)\)\]\s*pub\s+struct\s+{ctrl}", src)
        assert m, f"pyclass not found for {ctrl}"
        attrs = m.group(1)
        assert "extends=PyDoCtrlBase" in attrs or "extends = PyDoCtrlBase" in attrs


def test_SA_002_G08_expected_vm_module_split_files_exist() -> None:
    expected = [
        RUST_SRC / "dispatch.rs",
        RUST_SRC / "do_ctrl.rs",
        RUST_SRC / "rust_store.rs",
        RUST_SRC / "python_call.rs",
        RUST_SRC / "driver.rs",
    ]
    missing = [str(p.relative_to(ROOT)) for p in expected if not p.exists()]
    assert not missing, f"missing expected modules: {missing}"


def test_SA_002_G09_entry_no_generator_conversion_helpers() -> None:
    src = _read(RUST_SRC / "pyvm.rs")
    assert "fn to_generator_strict" not in src
    assert "fn start_with_generator" not in src
    body = _extract_fn_body(src, "start_with_expr")
    assert "DoCtrl::Eval" in body
    assert "program must be DoExpr" in body
