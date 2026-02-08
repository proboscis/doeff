"""TDD failing tests for spec audit SA-001 gaps G01-G26.

Each test targets a specific gap from the SA-001 audit report.
These tests are written BEFORE fixes and must FAIL on current code.
After fixes, all tests must PASS.

Phase 4 of the spec-gap-tdd workflow.

Spec references:
  - SPEC-008-rust-vm.md (Rev 11)
  - SPEC-009-rust-vm-migration.md (Rev 6)
  - SPEC-TYPES-001-program-effect-separation.md (Rev 9)
"""
from __future__ import annotations

import inspect
import pathlib
import re

import pytest

from doeff.effects import Get, Put
from doeff.program import GeneratorProgram
from doeff.rust_vm import default_handlers, run

RUST_SRC = pathlib.Path(__file__).resolve().parents[2] / "packages" / "doeff-vm" / "src"


def _prog(gen_factory):
    """Wrap a generator factory into a GeneratorProgram (has to_generator)."""
    return GeneratorProgram(gen_factory)


def _read_rust(filename: str) -> str:
    return (RUST_SRC / filename).read_text()


def _extract_fn_body(source: str, fn_name: str) -> str | None:
    m = re.search(rf"fn {fn_name}\(", source)
    if not m:
        return None
    start = m.start()
    depth, in_fn, end = 0, False, start
    for i, ch in enumerate(source[start:], start=start):
        if ch == "{":
            depth += 1
            in_fn = True
        elif ch == "}":
            depth -= 1
            if in_fn and depth == 0:
                end = i + 1
                break
    return source[start:end]


# ===========================================================================
# Critical Gaps (G01-G05)
# ===========================================================================


class TestSA001G01RunDefaults:
    """G01: run() defaults to default_handlers() — spec says handlers=[] by default."""

    def test_run_no_handlers_raises_unhandled(self):
        """run(program) with no handlers arg must NOT auto-install handlers."""

        def gen():
            yield Get("x")
            return 1

        # Spec: handlers=[] by default -> UnhandledEffect
        # Current: handlers=None -> default_handlers() -> succeeds
        result = run(_prog(gen), store={"x": 0})
        assert result.is_err(), "run() without handlers should fail (no state handler)"

    def test_run_empty_handlers_raises_unhandled(self):
        """run(program, handlers=[]) must raise UnhandledEffect for Get."""

        def gen():
            yield Get("x")
            return 1

        result = run(_prog(gen), handlers=[], store={"x": 0})
        assert result.is_err(), "run(handlers=[]) should fail for Get effect"


class TestSA001G02RawStore:
    """G02: RunResult missing raw_store property."""

    def test_result_has_raw_store(self):
        """RunResult must have .raw_store property per SPEC-009 section 2."""

        def gen():
            yield Put("x", 42)
            return "done"

        result = run(_prog(gen), handlers=default_handlers(), store={"x": 0})
        assert hasattr(result, "raw_store"), "RunResult missing .raw_store"

    def test_raw_store_reflects_final_state(self):
        """raw_store must contain final store after execution."""

        def gen():
            yield Put("x", 42)
            return "done"

        result = run(_prog(gen), handlers=default_handlers(), store={"x": 0})
        assert result.raw_store == {"x": 42}


class TestSA001G03ModifyReturnValue:
    """G03: Modify returns new_value not old_value."""

    def test_modify_returns_old_value(self):
        """Modify must return the OLD value (read-then-modify). SPEC-008 L1271."""
        from doeff.effects import Modify

        def gen():
            old = yield Modify("x", lambda v: v + 5)
            return old

        result = run(_prog(gen), handlers=default_handlers(), store={"x": 10})
        # Spec: old_value=10 returned. Impl currently returns 15 (new_value).
        assert result.value == 10, f"Modify returned {result.value}, expected 10 (old)"


class TestSA001G04Presets:
    """G04: doeff.presets module missing."""

    def test_import_sync_preset(self):
        """from doeff.presets import sync_preset must succeed. SPEC-009 section 7."""
        from doeff.presets import sync_preset  # noqa: F401

        assert sync_preset is not None
        assert isinstance(sync_preset, list)

    def test_import_async_preset(self):
        """from doeff.presets import async_preset must succeed."""
        from doeff.presets import async_preset  # noqa: F401

        assert async_preset is not None


class TestSA001G05ErrorProperty:
    """G05: RunResult missing .error property."""

    def test_result_has_error_property(self):
        """RunResult must have .error property per SPEC-009 section 2."""

        def gen():
            raise ValueError("boom")
            yield  # noqa: RET504

        result = run(_prog(gen), handlers=default_handlers())
        assert hasattr(result, "error"), "RunResult missing .error property"

    def test_error_returns_exception(self):
        """result.error must return the exception for Err results."""

        def gen():
            raise ValueError("boom")
            yield

        result = run(_prog(gen), handlers=default_handlers())
        assert isinstance(result.error, ValueError)


# ===========================================================================
# Moderate Gaps (G06-G20)
# ===========================================================================


class TestSA001G06PyclassEffects:
    """G06: No #[pyclass] effect structs (R11-A)."""

    def test_effect_rs_has_pyget_struct(self):
        """effect.rs must define #[pyclass] struct PyGet. SPEC-008 R11-A."""
        src = _read_rust("effect.rs")
        assert re.search(
            r"#\[pyclass.*\]\s*pub struct PyGet", src
        ), "effect.rs missing #[pyclass] PyGet struct"

    def test_effect_rs_has_pykpc_struct(self):
        """effect.rs must define #[pyclass] struct PyKPC."""
        src = _read_rust("effect.rs")
        assert re.search(
            r"#\[pyclass.*\]\s*pub struct PyKPC", src
        ), "effect.rs missing #[pyclass] PyKPC struct"


class TestSA001G07BasesWired:
    """G07: Rust bases not wired to Python types (R11-F)."""

    def test_python_effectbase_extends_rust(self):
        """Python EffectBase must be isinstance-compatible with Rust PyEffectBase."""
        from doeff_vm import EffectBase as RustEffectBase
        from doeff._types_internal import EffectBase

        assert issubclass(
            EffectBase, RustEffectBase
        ), "Python EffectBase doesn't extend Rust PyEffectBase"


class TestSA001G08ClassifyClean:
    """G08: classify_yielded duck-typing (R11-C)."""

    def test_classify_no_getattr_fallbacks(self):
        """classify_yielded must not use getattr/hasattr. SPEC-008 R11-C."""
        src = _read_rust("pyvm.rs")
        fn_body = _extract_fn_body(src, "classify_yielded")
        assert fn_body is not None, "classify_yielded function not found"
        assert "getattr" not in fn_body, "classify_yielded uses getattr (duck-typing)"
        assert "hasattr" not in fn_body, "classify_yielded uses hasattr (duck-typing)"
        assert "classify_yielded_fallback" not in fn_body, (
            "classify_yielded delegates to duck-typed fallback (gaming R11-C)"
        )


class TestSA001G09KpcRust:
    """G09: KPC not Rust #[pyclass]."""

    def test_kpc_importable_from_rust(self):
        """KleisliProgramCall (PyKPC) must be importable from doeff_vm."""
        from doeff_vm import KleisliProgramCall  # noqa: F401

        assert KleisliProgramCall is not None


class TestSA001G10AutoUnwrapHandler:
    """G10: auto_unwrap on KPC not handler (Rev 9)."""

    def test_kpc_has_no_strategy_field(self):
        """KPC must NOT store auto_unwrap_strategy. SPEC-TYPES-001 Rev 9."""
        from doeff.program import KleisliProgramCall

        fields = [f.name for f in KleisliProgramCall.__dataclass_fields__.values()]
        assert (
            "auto_unwrap_strategy" not in fields
        ), "KPC stores auto_unwrap_strategy -- should be handler-computed"


class TestSA001G11TypeHierarchy:
    """G11: DoExpr/DoThunk/DoCtrl = aliases."""

    def test_doexpr_dothunk_distinct(self):
        """DoExpr and DoThunk must be distinct types. SPEC-TYPES-001 section 1.4."""
        from doeff.program import DoExpr, DoThunk

        assert DoExpr is not DoThunk, "DoExpr and DoThunk are aliases (same object)"

    def test_doctrl_exists(self):
        """DoCtrl must exist as a distinct type."""
        from doeff.program import DoCtrl  # noqa: F401
        from doeff.program import DoExpr

        assert DoCtrl is not DoExpr, "DoCtrl should be distinct from DoExpr"


class TestSA001G12BaseClasses:
    """G12: EffectBase/KPC wrong base classes."""

    def test_effectbase_is_doexpr_subclass(self):
        """EffectBase must subclass DoExpr. SPEC-TYPES-001 section 1.4."""
        from doeff.program import DoExpr
        from doeff._types_internal import EffectBase

        assert issubclass(EffectBase, DoExpr), "EffectBase not a DoExpr subclass"

    def test_kpc_is_effectbase_subclass(self):
        """KPC must subclass EffectBase, not ProgramBase. SPEC-TYPES-001 section 1.4."""
        from doeff._types_internal import EffectBase
        from doeff.program import KleisliProgramCall

        assert issubclass(
            KleisliProgramCall, EffectBase
        ), "KPC extends ProgramBase, not EffectBase"


class TestSA001G13ExplicitKPC:
    """G13: Implicit KPC handler in run()."""

    def test_empty_handlers_no_kpc(self):
        """handlers=[] must not auto-install KPC handler. SPEC-TYPES-001 Q11."""
        from doeff import do

        @do
        def my_func(x: int):
            return x + 1

        result = run(my_func(1), handlers=[])
        assert result.is_err(), "KPC was handled despite handlers=[] (implicit KPC handler)"


class TestSA001G14SchedulerSentinel:
    """G14: Scheduler sentinel not Rust-exported."""

    def test_scheduler_from_doeff_vm(self):
        """doeff_vm must export scheduler sentinel."""
        import doeff_vm

        assert hasattr(doeff_vm, "scheduler"), "doeff_vm missing scheduler export"

    def test_scheduler_not_placeholder(self):
        """doeff.handlers.scheduler must not be a Python placeholder."""
        from doeff.handlers import scheduler

        assert not type(scheduler).__name__.startswith(
            "_Scheduler"
        ), f"scheduler is placeholder: {type(scheduler)}"


class TestSA001G15HandlerSigs:
    """G15: Handler trait sigs diverge."""

    def test_start_receives_py_and_bound(self):
        """RustHandlerProgram::start must receive py: Python<'_>. SPEC-008 L1111."""
        src = _read_rust("handler.rs")
        m = re.search(r"fn start\(&mut self,\s*(.+?)\)", src)
        assert m, "Could not find RustHandlerProgram::start"
        params = m.group(1)
        assert (
            "py:" in params or "Python" in params
        ), f"start() missing py parameter: start(&mut self, {params})"


class TestSA001G16DoCtrlExtends:
    """G16: DoCtrl pyclasses no extends=Base."""

    def test_with_handler_extends_doctrl_base(self):
        """PyWithHandler must have extends=PyDoCtrlBase. SPEC-008 R11-F."""
        src = _read_rust("pyvm.rs")
        m = re.search(r"#\[pyclass\(([^)]*)\)\]\s*pub struct PyWithHandler", src)
        assert m, "Could not find PyWithHandler pyclass"
        assert (
            "extends" in m.group(1)
        ), f"PyWithHandler missing extends: #[pyclass({m.group(1)})]"


class TestSA001G17StringAnnotations:
    """G17: String annot missing DoThunk/DoExpr."""

    def test_dothunk_annotation_prevents_unwrap(self):
        """'DoThunk[T]' annotation must prevent auto-unwrap."""
        from doeff.program import _string_annotation_is_program

        assert _string_annotation_is_program(
            "DoThunk[int]"
        ), "'DoThunk[int]' not recognized as program annotation"
        assert _string_annotation_is_program(
            "DoExpr[int]"
        ), "'DoExpr[int]' not recognized as program annotation"


class TestSA001G18Signature:
    """G18: run() signature defaults/types."""

    def test_handlers_default_is_empty_list(self):
        """run() handlers param must default to [] not None. SPEC-009 section 1."""
        from doeff.rust_vm import run

        sig = inspect.signature(run)
        default = sig.parameters["handlers"].default
        assert default == [] or default == () or default is inspect.Parameter.empty, (
            f"handlers default is {default!r}, expected []/() or no default"
        )


class TestSA001G19StrictToGenerator:
    """G19: to_generator too permissive."""

    def test_raw_generator_rejected(self):
        """run() must reject raw generators (not wrapped in ProgramBase)."""

        def gen():
            yield Get("x")
            return 1

        raw = gen()  # raw generator, not ProgramBase
        with pytest.raises((TypeError, Exception)):
            run(raw, handlers=default_handlers(), store={"x": 0})


class TestSA001G20TaskCompleted:
    """G20: TaskCompleted not public export."""

    def test_import_task_completed(self):
        """TaskCompleted must be importable from doeff.effects."""
        from doeff.effects import TaskCompleted  # noqa: F401

        assert TaskCompleted is not None


# ===========================================================================
# Minor Gaps (G21-G26)
# ===========================================================================


class TestSA001G21EffectEnum:
    """G21: Effect enum test-only remnant."""

    def test_no_effect_enum_in_runtime(self):
        """effect.rs runtime code must not define Effect enum. SPEC-008 R11-B."""
        src = _read_rust("effect.rs")
        runtime_src = re.sub(r"#\[cfg\(test\)\][\s\S]*?(?=\n#\[cfg|\Z)", "", src)
        assert not re.search(
            r"pub enum Effect\s*\{", runtime_src
        ), "Effect enum exists in runtime code (should be deleted per R11-B)"


# G22 through G24, G26: Spec documentation drift — fix-spec items, no failing test needed.
# - G22: Spec should use PyShared instead of Py<PyAny>
# - G23: Spec should use create_unstarted instead of create
# - G24: Spec should document PyException enum with lazy variants
# - G26: Spec should add + Sync to Callback type


class TestSA001G25RunResultProtocol:
    """G25: RunResult concrete not Protocol."""

    def test_run_result_is_protocol(self):
        """RunResult should be a Protocol, not a concrete class."""
        from doeff import RunResult

        is_protocol = getattr(RunResult, "_is_protocol", False)
        assert is_protocol, f"RunResult is {type(RunResult)}, not a Protocol"
