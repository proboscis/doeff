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

from doeff import Get, Put, default_handlers, run
from tests._run_helpers import run_with_defaults
# REMOVED: from doeff.program import GeneratorProgram

RUST_SRC = pathlib.Path(__file__).resolve().parents[2] / "packages" / "doeff-vm" / "src"
CORE_EFFECTS_SRC = pathlib.Path(__file__).resolve().parents[2] / "packages" / "doeff-core-effects" / "src"


def _prog(gen_factory):
    """Wrap a generator factory into a GeneratorProgram (has to_generator)."""
    return GeneratorProgram(gen_factory)


def _read_rust(filename: str) -> str:
    primary = RUST_SRC / filename
    if primary.exists():
        return primary.read_text()
    fallback = {
        "effect.rs": CORE_EFFECTS_SRC / "effects" / "mod.rs",
        "handler.rs": CORE_EFFECTS_SRC / "handlers" / "mod.rs",
        "scheduler.rs": CORE_EFFECTS_SRC / "scheduler" / "mod.rs",
    }.get(filename)
    if fallback is not None and fallback.exists():
        return fallback.read_text()
    return primary.read_text()


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





class TestSA001G04Presets:
    """G04: doeff.presets module missing."""



# ===========================================================================
# Moderate Gaps (G06-G20)
# ===========================================================================


class TestSA001G06PyclassEffects:
    """G06: No #[pyclass] effect structs (R11-A)."""


class TestSA001G07BasesWired:
    """G07: Rust bases not wired to Python types (R11-F)."""

    def test_python_effectbase_extends_rust(self):
        """Python EffectBase must be isinstance-compatible with Rust PyEffectBase."""
        from doeff_vm import EffectBase as RustEffectBase
        from doeff import EffectBase

        assert issubclass(EffectBase, RustEffectBase), (
            "Python EffectBase doesn't extend Rust PyEffectBase"
        )


class TestSA001G08ClassifyClean:
    """G08: classify_yielded duck-typing (R11-C)."""


class TestSA001G09KpcRust:
    """G09: legacy KPC symbols removed from Rust extension exports."""

    def test_legacy_kpc_symbols_not_importable_from_rust(self):
        """doeff_vm should not expose removed KPC symbols."""
        import doeff_vm

        assert not hasattr(doeff_vm, "Kleisli" + "ProgramCall")
        assert not hasattr(doeff_vm, "Py" + "KPC")


class TestSA001G10AutoUnwrapHandler:
    """G10: auto_unwrap strategy should stay on callable metadata, not call nodes."""

    def test_call_node_has_no_strategy_field(self):
        """Call DoCtrl instances must not carry _auto_unwrap_strategy state."""
        from doeff import do

        @do
        def identity(x: int):
            return x

        call_node = identity(1)
        assert not hasattr(call_node, "_auto_unwrap_strategy"), (
            "Call node stores _auto_unwrap_strategy -- should be computed on callable"
        )


class TestSA001G11TypeHierarchy:
    """G11: DoExpr/DoThunk/DoCtrl = aliases."""


class TestSA001G12BaseClasses:
    """G12: EffectBase/KPC wrong base classes."""

    def test_do_call_result_is_not_effectbase(self):
        """@do call results should be DoCtrl, not EffectBase values."""
        from doeff import do
        from doeff import EffectBase

        @do
        def identity(x: int):
            return x

        assert not isinstance(identity(1), EffectBase)


class TestSA001G13ExplicitKPC:
    """G13: Implicit KPC handler in run()."""


class TestSA001G14SchedulerSentinel:
    """G14: Scheduler sentinel not Rust-exported."""


class TestSA001G15HandlerSigs:
    """G15: Handler trait sigs diverge."""


class TestSA001G16DoCtrlExtends:
    """G16: DoCtrl pyclasses no extends=Base."""


class TestSA001G17ProgramAnnotations:
    """G17: Program-kind detection should use resolved type objects."""


class TestSA001G18Signature:
    """G18: run() signature defaults/types."""



class TestSA001G20TaskCompleted:
    """G20: TaskCompleted not public export."""


# ===========================================================================
# Minor Gaps (G21-G26)
# ===========================================================================


class TestSA001G21EffectEnum:
    """G21: Effect enum test-only remnant."""


# G22 through G24, G26: Spec documentation drift — fix-spec items, no failing test needed.
# - G22: Spec should use PyShared instead of Py<PyAny>
# - G23: Spec should use create_unstarted instead of create
# - G24: Spec should document PyException enum with lazy variants
# - G26: Spec should add + Sync to Callback type


class TestSA001G25RunResultProtocol:
    """G25: RunResult concrete not Protocol."""
