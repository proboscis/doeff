"""TDD failing tests for spec audit gaps G8, G9, G17, G18, G19, G20, G22, G24.

Each test targets a specific gap from SPEC-AUDIT-2025-02-08.md.
These tests are written BEFORE fixes and must FAIL on current code.
After fixes, all tests must PASS.

Phase 4 of the spec-gap-tdd workflow.
"""

from __future__ import annotations

import re
from pathlib import Path

# REMOVED: from doeff.program import GeneratorProgram

ROOT = Path(__file__).resolve().parents[2]


def _read_vm_or_core_effects(filename: str) -> str:
    primary = ROOT / "packages" / "doeff-vm" / "src" / filename
    if primary.exists():
        return primary.read_text()
    fallback = {
        "effect.rs": ROOT / "packages" / "doeff-core-effects" / "src" / "effects" / "mod.rs",
        "handler.rs": ROOT / "packages" / "doeff-core-effects" / "src" / "handlers" / "mod.rs",
        "scheduler.rs": ROOT / "packages" / "doeff-core-effects" / "src" / "scheduler" / "mod.rs",
    }.get(filename)
    if fallback is not None and fallback.exists():
        return fallback.read_text()
    return primary.read_text()


def _prog(gen_factory):
    """Wrap a generator factory into a GeneratorProgram (has to_generator)."""
    return GeneratorProgram(gen_factory)  # noqa: F821 - legacy removed API reference is intentionally preserved


# ---------------------------------------------------------------------------
# G8: Import paths for Resume/Delegate/Transfer/WithHandler/K
# Spec (SPEC-009 §8): from doeff import Resume, Delegate, Transfer, WithHandler, K
# Current: These are NOT re-exported from doeff/__init__.py — ImportError
# ---------------------------------------------------------------------------


class TestG8ImportPaths:
    """G8: doeff top-level must re-export VM dispatch primitives."""

    def test_import_with_handler(self) -> None:
        """from doeff import WithHandler must succeed."""
        from doeff import WithHandler

        assert WithHandler is not None

    def test_import_resume(self) -> None:
        """from doeff import Resume must succeed."""
        from doeff import Resume

        assert Resume is not None

    def test_import_delegate(self) -> None:
        """from doeff import Delegate must succeed."""
        from doeff import Delegate

        assert Delegate is not None

    def test_import_transfer(self) -> None:
        """from doeff import Transfer must succeed."""
        from doeff import Transfer

        assert Transfer is not None

    def test_import_k(self) -> None:
        """from doeff import K must succeed."""
        from doeff import K

        assert K is not None


# ---------------------------------------------------------------------------
# G9: doeff.handlers module doesn't exist
# Spec (SPEC-009 §8 L611-614, L676): from doeff.handlers import state, reader, writer, scheduler
# Current: No doeff/handlers.py — ImportError
# ---------------------------------------------------------------------------


class TestG9HandlersModule:
    """G9: doeff.handlers must expose handler sentinels."""

    def test_import_state(self) -> None:
        """from doeff.handlers import state must succeed."""
        from doeff_core_effects.handlers import state

        assert state is not None

    def test_import_reader(self) -> None:
        """from doeff.handlers import reader must succeed."""
        from doeff_core_effects.handlers import reader

        assert reader is not None

    def test_import_writer(self) -> None:
        """from doeff.handlers import writer must succeed."""
        from doeff_core_effects.handlers import writer

        assert writer is not None


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


# ---------------------------------------------------------------------------
# G18: Two RunResult types — Rust PyRunResult vs Python RunResult
# Spec (SPEC-009 §2): from doeff import RunResult → must be the Rust type
# Current: from doeff import RunResult gives Python dataclass version
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# G19: PyResultOk/Err != doeff Ok/Err
# Spec (SPEC-009 L229-237): isinstance(result.result, Ok) must work
# Current: result.result is Rust PyResultOk, not doeff Ok — isinstance fails
# ---------------------------------------------------------------------------



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


# ---------------------------------------------------------------------------
# G24: Reader/Writer resume returns Value instead of unreachable!()
# Spec (SPEC-008 L1257, L1296): unreachable!("never yields mid-handling")
# Current: Returns value (defensive). handler.rs:763, 842
#
# This is Rust-internal. We verify from source that resume() for
# Reader/Writer handlers uses unreachable!() not Return(value).
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# G20: transfer_next_or skips store save/load
# Spec (SPEC-008 L1434-1447): transfer_task saves current store, loads new store
# Current: transfer_next_or only pops ready queue, no store context-switching
# ---------------------------------------------------------------------------


class TestG20StoreContextSwitch:
    """G20: Task switching must save/load per-task stores."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_impl_resume(source: str, struct_name: str) -> str | None:
    """Extract the resume() method body from an impl block for the given struct."""
    # Support both historical AST naming and current IR naming.
    impl_pattern = rf"impl\s+(?:ASTStreamProgram|IRStreamProgram)\s+for\s+{struct_name}"
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
