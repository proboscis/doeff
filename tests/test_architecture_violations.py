"""Architecture violation detection tests.

These tests enforce the OCaml 5 alignment invariants from SPEC-VM-020.
They run as part of the normal pytest suite and catch violations
that would otherwise require manual code review.

The principle: "the fiber chain IS the state."
No accumulated state, no identity tracking, no stored fiber ID lists.
"""

import subprocess
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VM_CORE_SRC = os.path.join(REPO_ROOT, "packages", "doeff-vm-core", "src")
CORE_EFFECTS_SRC = os.path.join(REPO_ROOT, "packages", "doeff-core-effects", "src")
VM_SRC = os.path.join(REPO_ROOT, "packages", "doeff-vm", "src")
ALL_SRC_DIRS = [VM_CORE_SRC, CORE_EFFECTS_SRC, VM_SRC]


def _grep_rust(pattern: str, dirs: list[str] | None = None) -> list[str]:
    """Search Rust files for a pattern using ripgrep."""
    dirs = dirs or ALL_SRC_DIRS
    existing = [d for d in dirs if os.path.isdir(d)]
    if not existing:
        return []
    result = subprocess.run(
        ["rg", "--no-heading", "-n", "--type", "rs", pattern] + existing,
        capture_output=True, text=True,
    )
    return [line for line in result.stdout.strip().split("\n") if line]


def _filter_non_test_non_comment(lines: list[str]) -> list[str]:
    """Filter out test files and comment-only matches."""
    filtered = []
    for line in lines:
        # Skip test files
        if "test" in line.lower().split(":")[0]:
            continue
        # Skip comments
        parts = line.split(":", 2)
        if len(parts) >= 3:
            code = parts[2].strip()
            if code.startswith("//") or code.startswith("///"):
                continue
        filtered.append(line)
    return filtered


class TestNoContId:
    """ContId must not exist — OCaml 5 has no continuation identity."""

    def test_no_cont_id_struct(self):
        matches = _grep_rust(r"struct ContId")
        matches = _filter_non_test_non_comment(matches)
        assert matches == [], f"ContId struct found:\n" + "\n".join(matches)

    def test_no_cont_id_usage(self):
        matches = _grep_rust(r"ContId(?!.*removed)")
        matches = _filter_non_test_non_comment(matches)
        # Filter out the "// ContId removed" comments
        matches = [m for m in matches if "removed" not in m and "ContId" in m.split(":", 2)[-1]]
        assert matches == [], f"ContId usage found:\n" + "\n".join(matches)


class TestNoCopyMachine:
    """No method should create a Continuation from stored data."""

    def test_no_clone_handle(self):
        matches = _grep_rust(r"clone_handle\(\)")
        matches = _filter_non_test_non_comment(matches)
        assert matches == [], f"clone_handle() found:\n" + "\n".join(matches)

    def test_no_capture_from_fiber_ids(self):
        matches = _grep_rust(r"capture_from_fiber_ids")
        matches = _filter_non_test_non_comment(matches)
        assert matches == [], f"capture_from_fiber_ids found:\n" + "\n".join(matches)

    def test_no_continuation_from_topology(self):
        matches = _grep_rust(r"continuation_from_topology")
        matches = _filter_non_test_non_comment(matches)
        assert matches == [], f"continuation_from_topology found:\n" + "\n".join(matches)


class TestNoSharedOwnership:
    """Continuations must be move-only, no shared flags."""

    def test_no_arc_atomicbool_in_continuation(self):
        matches = _grep_rust(r"Arc.*AtomicBool", dirs=[VM_CORE_SRC])
        # Only check continuation.rs
        matches = [m for m in matches if "continuation.rs" in m]
        assert matches == [], f"Arc<AtomicBool> in continuation:\n" + "\n".join(matches)

    def test_continuation_not_clone(self):
        """Continuation must not derive or implement Clone."""
        matches = _grep_rust(r"impl Clone for Continuation", dirs=[VM_CORE_SRC])
        matches += _grep_rust(r"derive.*Clone.*Continuation|Continuation.*derive.*Clone", dirs=[VM_CORE_SRC])
        matches = _filter_non_test_non_comment(matches)
        assert matches == [], f"Continuation implements Clone:\n" + "\n".join(matches)


class TestNoStoredFiberIdLists:
    """No Vec<FiberId> should be stored as chain representation.

    The only legitimate Vec<FiberId> is the return value of walk_chain()
    which is computed on demand from parent pointers.
    """

    def test_no_vec_fiber_id_on_structs(self):
        """Struct fields should not store Vec<FiberId> for chain data."""
        matches = _grep_rust(r"Vec<FiberId>", dirs=[VM_CORE_SRC])
        matches = _filter_non_test_non_comment(matches)
        # Allow walk_chain return type and function signatures
        violations = []
        for m in matches:
            code = m.split(":", 2)[-1].strip() if ":" in m else m
            # Allow: fn walk_chain(...) -> Vec<FiberId>
            if "fn " in code and "->" in code:
                continue
            # Allow: let chain: Vec<FiberId> = (local variable)
            if code.startswith("let "):
                continue
            violations.append(m)
        assert violations == [], (
            f"Vec<FiberId> stored on struct (should use FiberId head + parent walk):\n"
            + "\n".join(violations)
        )


class TestNoProgramDispatch:
    """ProgramDispatch must not exist — OCaml 5 has no dispatch state."""

    def test_no_program_dispatch_struct(self):
        matches = _grep_rust(r"struct ProgramDispatch")
        matches = _filter_non_test_non_comment(matches)
        assert matches == [], f"ProgramDispatch struct found:\n" + "\n".join(matches)


class TestNoAccumulatedTraceState:
    """TraceState and DebugState must not exist on the VM."""

    def test_no_trace_state(self):
        matches = _grep_rust(r"TraceState", dirs=[VM_CORE_SRC])
        matches = _filter_non_test_non_comment(matches)
        assert matches == [], f"TraceState found:\n" + "\n".join(matches)

    def test_no_debug_state_on_vm(self):
        matches = _grep_rust(r"debug.*DebugState|DebugState.*debug", dirs=[VM_CORE_SRC])
        matches = _filter_non_test_non_comment(matches)
        assert matches == [], f"DebugState on VM found:\n" + "\n".join(matches)


class TestFiberThreeFields:
    """Fiber should have only: frames, parent, handler."""

    def test_no_pending_fields_on_fiber(self):
        """Fiber must not have pending_* fields."""
        matches = _grep_rust(r"pending_", dirs=[VM_CORE_SRC])
        # Only check segment.rs (Fiber definition)
        matches = [m for m in matches if "segment.rs" in m]
        matches = _filter_non_test_non_comment(matches)
        assert matches == [], f"pending_* fields on Fiber:\n" + "\n".join(matches)

    def test_no_interceptor_fields_on_fiber(self):
        """Fiber must not have interceptor_* fields."""
        matches = _grep_rust(r"interceptor_", dirs=[VM_CORE_SRC])
        matches = [m for m in matches if "segment.rs" in m]
        matches = _filter_non_test_non_comment(matches)
        assert matches == [], f"interceptor_* fields on Fiber:\n" + "\n".join(matches)
