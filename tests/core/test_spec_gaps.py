"""TDD failing tests for spec audit gaps G8, G9, G17, G18, G19, G20, G22, G24.

Each test targets a specific gap from SPEC-AUDIT-2025-02-08.md.
These tests are written BEFORE fixes and must FAIL on current code.
After fixes, all tests must PASS.

Phase 4 of the spec-gap-tdd workflow.
"""

from __future__ import annotations

import re

import pytest

from doeff.effects import Ask, Get, Put, Tell
from doeff.program import GeneratorProgram
from doeff.rust_vm import default_handlers, run


def _prog(gen_factory):
    """Wrap a generator factory into a GeneratorProgram (has to_generator)."""
    return GeneratorProgram(gen_factory)


# ---------------------------------------------------------------------------
# G8: Import paths for Resume/Delegate/Transfer/WithHandler/K
# Spec (SPEC-009 §8): from doeff import Resume, Delegate, Transfer, WithHandler, K
# Current: These are NOT re-exported from doeff/__init__.py — ImportError
# ---------------------------------------------------------------------------


class TestG8ImportPaths:
    """G8: doeff top-level must re-export VM dispatch primitives."""

    def test_import_with_handler(self) -> None:
        """from doeff import WithHandler must succeed."""
        from doeff import WithHandler  # noqa: F401

        assert WithHandler is not None

    def test_import_resume(self) -> None:
        """from doeff import Resume must succeed."""
        from doeff import Resume  # noqa: F401

        assert Resume is not None

    def test_import_delegate(self) -> None:
        """from doeff import Delegate must succeed."""
        from doeff import Delegate  # noqa: F401

        assert Delegate is not None

    def test_import_transfer(self) -> None:
        """from doeff import Transfer must succeed."""
        from doeff import Transfer  # noqa: F401

        assert Transfer is not None

    def test_import_k(self) -> None:
        """from doeff import K must succeed."""
        from doeff import K  # noqa: F401

        assert K is not None

    def test_identity_matches_doeff_vm(self) -> None:
        """doeff.WithHandler is doeff_vm.WithHandler (same object)."""
        import doeff
        import doeff_vm

        assert doeff.WithHandler is doeff_vm.WithHandler
        assert doeff.Resume is doeff_vm.Resume
        assert doeff.Delegate is doeff_vm.Delegate
        assert doeff.Transfer is doeff_vm.Transfer
        assert doeff.K is doeff_vm.K


# ---------------------------------------------------------------------------
# G9: doeff.handlers module doesn't exist
# Spec (SPEC-009 §8 L611-614, L676): from doeff.handlers import state, reader, writer, scheduler
# Current: No doeff/handlers.py — ImportError
# ---------------------------------------------------------------------------


class TestG9HandlersModule:
    """G9: doeff.handlers must expose handler sentinels."""

    def test_import_state(self) -> None:
        """from doeff.handlers import state must succeed."""
        from doeff.handlers import state  # noqa: F401

        assert state is not None

    def test_import_reader(self) -> None:
        """from doeff.handlers import reader must succeed."""
        from doeff.handlers import reader  # noqa: F401

        assert reader is not None

    def test_import_writer(self) -> None:
        """from doeff.handlers import writer must succeed."""
        from doeff.handlers import writer  # noqa: F401

        assert writer is not None

    def test_import_scheduler(self) -> None:
        """from doeff.handlers import scheduler must succeed."""
        from doeff.handlers import scheduler  # noqa: F401

        assert scheduler is not None

    def test_identity_matches_doeff_vm(self) -> None:
        """doeff.handlers.state is doeff_vm.state (same object)."""
        import doeff_vm
        from doeff.handlers import reader, state, writer

        assert state is doeff_vm.state
        assert reader is doeff_vm.reader
        assert writer is doeff_vm.writer


# ---------------------------------------------------------------------------
# G17: Scheduler silently swallows errors
# Spec (SPEC-008 L1668-1702): unexpected resume → Throw(TypeError/RuntimeError)
# Current: Returns Value::None silently (scheduler.rs:719, 754-756)
#
# NOTE: This is a Rust-internal behavior. From Python we verify the symptom:
# the scheduler's resume error paths must throw instead of returning None.
# We check that pyvm.rs:scheduler.rs L719 (bad continuation type) and L756
# (unexpected idle resume) are properly tested via Rust unit tests.
# Here we add a source-level assertion as a canary.
# ---------------------------------------------------------------------------


class TestG17SchedulerErrorPropagation:
    """G17: Scheduler must raise on unexpected conditions, not return None."""

    def test_error_path_returns_throw_not_none(self) -> None:
        """scheduler.rs resume() must not contain 'Return(Value::None)' in error paths."""
        import pathlib

        scheduler_src = pathlib.Path(
            "/Users/s22625/repos/doeff/packages/doeff-vm/src/scheduler.rs"
        ).read_text()

        # Find the resume() function body — look for Return(Value::None) which is
        # the error-swallowing pattern. After fix, these should be Throw(...).
        # A crude but effective source-level canary.
        matches = list(re.finditer(r"Return\(Value::None\)", scheduler_src))
        assert len(matches) == 0, (
            f"Found {len(matches)} instances of Return(Value::None) in scheduler.rs resume(). "
            "Spec requires Throw(TypeError/RuntimeError) for error paths."
        )


# ---------------------------------------------------------------------------
# G18: Two RunResult types — Rust PyRunResult vs Python RunResult
# Spec (SPEC-009 §2): from doeff import RunResult → must be the Rust type
# Current: from doeff import RunResult gives Python dataclass version
# ---------------------------------------------------------------------------


class TestG18RunResultUnification:
    """G18: doeff.RunResult must be the Rust VM RunResult."""

    def test_run_result_type_matches(self) -> None:
        """run() result must be isinstance of doeff.RunResult."""
        from doeff import RunResult

        def gen():
            return 42
            yield  # noqa: RET504

        result = run(_prog(gen), handlers=default_handlers())
        assert isinstance(result, RunResult), (
            f"run() returned {type(result).__module__}.{type(result).__name__}, "
            f"expected doeff.RunResult"
        )

    def test_run_result_has_raw_store(self) -> None:
        """doeff.RunResult must have .raw_store attribute (Rust type does, Python doesn't)."""

        def gen():
            return 42
            yield  # noqa: RET504

        result = run(_prog(gen), handlers=default_handlers())
        assert hasattr(result, "raw_store"), "RunResult missing .raw_store (Python type, not Rust)"


# ---------------------------------------------------------------------------
# G19: PyResultOk/Err != doeff Ok/Err
# Spec (SPEC-009 L229-237): isinstance(result.result, Ok) must work
# Current: result.result is Rust PyResultOk, not doeff Ok — isinstance fails
# ---------------------------------------------------------------------------


class TestG19OkErrUnification:
    """G19: doeff.Ok and doeff.Err must match what run().result returns."""

    def test_ok_isinstance(self) -> None:
        """isinstance(result.result, doeff.Ok) must be True for successful run."""
        from doeff import Ok, do

        @do
        def program():
            if False:
                yield
            return 42

        result = run(program(), handlers=default_handlers())
        r = result.result
        assert isinstance(r, Ok), (
            f"result.result is {type(r).__module__}.{type(r).__name__}, not doeff.Ok"
        )

    def test_err_isinstance(self) -> None:
        """isinstance(result.result, doeff.Err) must be True for failed run."""
        from doeff import Err, do

        @do
        def program():
            raise ValueError("boom")
            if False:
                yield

        result = run(program(), handlers=default_handlers())
        r = result.result
        assert isinstance(r, Err), (
            f"result.result is {type(r).__module__}.{type(r).__name__}, not doeff.Err"
        )

    def test_strict_run_rejects_generator_program_callable(self) -> None:
        """Strict run() should accept Program wrappers exposing to_generator()."""

        def gen():
            return 42
            yield  # noqa: RET504

        result = run(_prog(gen), handlers=default_handlers())
        assert result.value == 42


# ---------------------------------------------------------------------------
# G22: Base classes missing `frozen` attribute
# Spec (SPEC-008 R11-F L907-920): #[pyclass(subclass, frozen, name=...)]
# Current: #[pyclass(subclass, name=...)] — no frozen
#
# This is an ARCHITECTURAL gap. Since PyO3 base classes can't be instantiated
# directly from Python, we check the Rust source code for the frozen attribute.
# ---------------------------------------------------------------------------


class TestG22FrozenBases:
    """G22: Core VM base classes must include frozen attribute."""

    def test_pyclass_declarations_include_frozen(self) -> None:
        """All three base class #[pyclass] macros must include 'frozen'."""
        import pathlib

        pyvm_src = pathlib.Path(
            "/Users/s22625/repos/doeff/packages/doeff-vm/src/pyvm.rs"
        ).read_text()

        bases = ["DoExprBase", "EffectBase", "DoCtrlBase"]
        for name in bases:
            # Find the #[pyclass(...)] line preceding the struct with this name
            pattern = rf"#\[pyclass\(([^)]*)\)\]\s*pub struct Py{name}"
            m = re.search(pattern, pyvm_src)
            assert m is not None, f"Could not find #[pyclass] for Py{name}"
            attrs = m.group(1)
            assert "frozen" in attrs, (
                f"Py{name} #[pyclass({attrs})] is missing 'frozen' attribute. "
                f'Spec requires #[pyclass(subclass, frozen, name="{name}")]'
            )


# ---------------------------------------------------------------------------
# G24: Reader/Writer resume returns Value instead of unreachable!()
# Spec (SPEC-008 L1257, L1296): unreachable!("never yields mid-handling")
# Current: Returns value (defensive). handler.rs:763, 842
#
# This is Rust-internal. We verify from source that resume() for
# Reader/Writer handlers uses unreachable!() not Return(value).
# ---------------------------------------------------------------------------


class TestG24HandlerResumeSemantics:
    """G24: Reader/Writer handlers resume() must be unreachable, not Return."""

    def test_reader_handler_resume_is_unreachable(self) -> None:
        """ReaderHandlerProgram::resume must be unreachable for one-shot lookup."""
        import pathlib

        handler_src = pathlib.Path(
            pathlib.Path(__file__).resolve().parents[2]
            / "packages"
            / "doeff-vm"
            / "src"
            / "handler.rs"
        ).read_text()

        # Find ReaderHandlerProgram's resume method
        # Look for the impl block and its resume fn
        reader_section = _extract_impl_resume(handler_src, "ReaderHandlerProgram")
        assert reader_section is not None, "Could not find ReaderHandlerProgram::resume"

        assert "unreachable!" in reader_section, (
            "ReaderHandlerProgram::resume should be unreachable for pure Ask lookup, "
            f"but contains: {reader_section.strip()}"
        )

    def test_writer_handler_resume_is_unreachable(self) -> None:
        """WriterHandlerProgram::resume must use unreachable!(), not Return."""
        import pathlib

        handler_src = pathlib.Path(
            pathlib.Path(__file__).resolve().parents[2]
            / "packages"
            / "doeff-vm"
            / "src"
            / "handler.rs"
        ).read_text()

        writer_section = _extract_impl_resume(handler_src, "WriterHandlerProgram")
        assert writer_section is not None, "Could not find WriterHandlerProgram::resume"

        assert "unreachable!" in writer_section, (
            "WriterHandlerProgram::resume should use unreachable!() per spec, "
            f"but contains: {writer_section.strip()}"
        )

    def test_reader_one_shot_behavior(self) -> None:
        """Ask effect returns value in one shot — no intermediate yield."""

        def gen():
            val = yield Ask("key")
            return val

        result = run(_prog(gen), handlers=default_handlers(), env={"key": "hello"})
        assert result.value == "hello"

    def test_writer_one_shot_behavior(self) -> None:
        """Tell effect returns unit in one shot — no intermediate yield."""

        def gen():
            yield Tell("message")
            return "done"

        result = run(_prog(gen), handlers=default_handlers())
        assert result.value == "done"


# ---------------------------------------------------------------------------
# G20: transfer_next_or skips store save/load
# Spec (SPEC-008 L1434-1447): transfer_task saves current store, loads new store
# Current: transfer_next_or only pops ready queue, no store context-switching
# ---------------------------------------------------------------------------


class TestG20StoreContextSwitch:
    """G20: Task switching must save/load per-task stores."""

    def test_transfer_next_or_saves_and_loads_store(self) -> None:
        """transfer_next_or must save/load stores during task switching (source check).

        Spec (SPEC-008 L1434-1447) requires:
        1. Save current task's store before switching
        2. Load new task's store after switching

        Current impl only pops from ready queue without any store save/load.
        """
        import pathlib

        scheduler_src = pathlib.Path(
            "/Users/s22625/repos/doeff/packages/doeff-vm/src/scheduler.rs"
        ).read_text()

        # Find the transfer_next_or function
        fn_match = re.search(
            r"pub fn transfer_next_or\(.*?\{(.+?)\n    \}",
            scheduler_src,
            re.DOTALL,
        )
        assert fn_match is not None, "Could not find transfer_next_or function"

        fn_body = fn_match.group(1)

        # The function must reference store save/load operations.
        # The _store parameter should NOT be prefixed with _ (meaning it's used).
        has_store_usage = (
            "store" in fn_body.replace("_store", "")
            or "save_store" in fn_body
            or "load_store" in fn_body
            or "task_store" in fn_body.lower()
        )
        assert has_store_usage, (
            "transfer_next_or does not save/load stores during task switching. "
            "The store parameter is unused (_store). Spec requires saving current "
            "task store and loading new task store on context switch."
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_impl_resume(source: str, struct_name: str) -> str | None:
    """Extract the resume() method body from an impl block for the given struct."""
    # Find "impl RustHandlerProgram for <struct_name>"
    impl_pattern = rf"impl\s+RustHandlerProgram\s+for\s+{struct_name}"
    m = re.search(impl_pattern, source)
    if m is None:
        return None

    # From the impl block, find fn resume
    rest = source[m.start() :]
    resume_match = re.search(r"fn resume\(", rest)
    if resume_match is None:
        return None

    # Extract until the next fn or closing brace at the same depth
    start = resume_match.start()
    brace_depth = 0
    in_fn = False
    end = start
    for i, ch in enumerate(rest[start:], start=start):
        if ch == "{":
            brace_depth += 1
            in_fn = True
        elif ch == "}":
            brace_depth -= 1
            if in_fn and brace_depth == 0:
                end = i + 1
                break

    return rest[start:end]
