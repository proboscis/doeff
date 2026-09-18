"""Architecture violation detection tests.

These tests enforce the OCaml 5 alignment invariants from SPEC-VM-020.
They run as part of the normal pytest suite and catch violations
that would otherwise require manual code review.

The principle: "the fiber chain IS the state."
No accumulated state, no identity tracking, no stored fiber ID lists.

Contract: this check depends on nothing installed on the host that runs it — the
answer is the same on a laptop, on the company Mac, on zeus and inside a k3s pod.
The scan is in-process (pathlib + re): no external command, and no git metadata
either, because `gate.full` ships the tree to the runner without its `.git`.

Why the contract is written down (measured 2026-09-19 on cross-section 97e8c3a8):
the scan used to shell out to `rg`, which made the answer depend on the host in
two opposite ways. With `rg` off PATH, all 13 tests raised
`FileNotFoundError: 'rg'` — that is the 13/13 red the daily verification of
2026-09-19 reported. With `rg` on PATH, `--type rs` is not a ripgrep file type
(the Rust type is spelled `rust`), so rg exited 2 with empty stdout and all 13
tests passed vacuously, scanning nothing. Both modes are gone once the scan runs
in-process, and a violation now names the repo-relative path and line, so a red
run says which file is at fault instead of which host it ran on.

The half of that contract a green run cannot show — that an empty scan is an
error and not "no violations" — is itself checked, by
`TestEmptyScanIsNotNoViolations` below.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
VM_CORE_SRC = REPO_ROOT / "packages" / "doeff-vm-core" / "src"
CORE_EFFECTS_SRC = REPO_ROOT / "packages" / "doeff-core-effects" / "src"
VM_SRC = REPO_ROOT / "packages" / "doeff-vm" / "src"
ALL_SRC_DIRS = [VM_CORE_SRC, CORE_EFFECTS_SRC, VM_SRC]


def _grep_rust(pattern: str, dirs: list[Path] | None = None) -> list[str]:
    """Search `*.rs` under `dirs` for `pattern`; return "<rel path>:<line no>:<line>".

    Pure Python on purpose — see the module docstring. A directory that does not
    exist is skipped (CORE_EFFECTS_SRC carries no Rust tree today), but a call
    where *none* of the requested directories exist is a hard failure: an empty
    scan must never read as "no violations".
    """
    dirs = dirs or ALL_SRC_DIRS
    existing = [d for d in dirs if d.is_dir()]
    assert existing, (
        "no source directory to scan — refusing to report 'no violations' from an "
        "empty scan. Requested: " + ", ".join(d.relative_to(REPO_ROOT).as_posix() for d in dirs)
    )
    regex = re.compile(pattern)
    matches: list[str] = []
    for directory in existing:
        for path in sorted(directory.rglob("*.rs")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace")
            for line_no, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    matches.append(f"{rel}:{line_no}:{line}")
    return matches


def _filter_non_test_non_comment(lines: list[str]) -> list[str]:
    """Filter out test files and comment-only matches.

    The path field is repo-relative (see `_grep_rust`). It used to be the
    absolute path handed to `rg`, so any checkout whose own path contained
    "test" — a worktree named `doeff-wt-hostdep-tests`, say — silently dropped
    every real violation along with the test files.
    """
    filtered = []
    for line in lines:
        # Skip test files
        if "test" in line.lower().split(":")[0]:
            continue
        # Skip comments
        parts = line.split(":", 2)
        if len(parts) >= 3:
            code = parts[2].strip()
            if code.startswith(("//", "///")):
                continue
        filtered.append(line)
    return filtered


class TestEmptyScanIsNotNoViolations:
    """The scanner must refuse an empty scan instead of reporting no violations.

    Guards the module contract above. A scan that silently covers nothing comes
    back green, so without this the 13 checks below could all pass while
    checking nothing — which is exactly what `rg --type rs` (not a ripgrep file
    type) did until 2026-09-19.
    """

    def test_scan_with_no_existing_directory_is_an_error(self):
        absent = REPO_ROOT / "packages" / "doeff-no-such-package" / "src"

        with pytest.raises(AssertionError) as excinfo:
            _grep_rust(r"ContId", dirs=[absent])
        assert "packages/doeff-no-such-package/src" in str(excinfo.value)

        # One real directory is enough to scan: a missing one beside it is
        # normal (CORE_EFFECTS_SRC carries no Rust tree today), not an error.
        assert _grep_rust(r"struct Continuation", dirs=[absent, VM_CORE_SRC])


class TestNoContId:
    """ContId must not exist — OCaml 5 has no continuation identity."""

    def test_no_cont_id_struct(self):
        matches = _grep_rust(r"struct ContId")
        matches = _filter_non_test_non_comment(matches)
        assert matches == [], "ContId struct found:\n" + "\n".join(matches)

    def test_no_cont_id_usage(self):
        matches = _grep_rust(r"ContId(?!.*removed)")
        matches = _filter_non_test_non_comment(matches)
        # Filter out the "// ContId removed" comments
        matches = [m for m in matches if "removed" not in m and "ContId" in m.split(":", 2)[-1]]
        assert matches == [], "ContId usage found:\n" + "\n".join(matches)


class TestNoCopyMachine:
    """No method should create a Continuation from stored data."""

    def test_no_clone_handle(self):
        matches = _grep_rust(r"clone_handle\(\)")
        matches = _filter_non_test_non_comment(matches)
        assert matches == [], "clone_handle() found:\n" + "\n".join(matches)

    def test_no_capture_from_fiber_ids(self):
        matches = _grep_rust(r"capture_from_fiber_ids")
        matches = _filter_non_test_non_comment(matches)
        assert matches == [], "capture_from_fiber_ids found:\n" + "\n".join(matches)

    def test_no_continuation_from_topology(self):
        matches = _grep_rust(r"continuation_from_topology")
        matches = _filter_non_test_non_comment(matches)
        assert matches == [], "continuation_from_topology found:\n" + "\n".join(matches)


class TestNoSharedOwnership:
    """Continuations must be move-only, no shared flags."""

    def test_no_arc_atomicbool_in_continuation(self):
        matches = _grep_rust(r"Arc.*AtomicBool", dirs=[VM_CORE_SRC])
        # Only check continuation.rs
        matches = [m for m in matches if "continuation.rs" in m]
        assert matches == [], "Arc<AtomicBool> in continuation:\n" + "\n".join(matches)

    def test_continuation_not_clone(self):
        """Continuation must not derive or implement Clone."""
        matches = _grep_rust(r"impl Clone for Continuation", dirs=[VM_CORE_SRC])
        matches += _grep_rust(r"derive.*Clone.*Continuation|Continuation.*derive.*Clone", dirs=[VM_CORE_SRC])
        matches = _filter_non_test_non_comment(matches)
        assert matches == [], "Continuation implements Clone:\n" + "\n".join(matches)


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
            "Vec<FiberId> stored on struct (should use FiberId head + parent walk):\n"
            + "\n".join(violations)
        )


class TestNoProgramDispatch:
    """ProgramDispatch must not exist — OCaml 5 has no dispatch state."""

    def test_no_program_dispatch_struct(self):
        matches = _grep_rust(r"struct ProgramDispatch")
        matches = _filter_non_test_non_comment(matches)
        assert matches == [], "ProgramDispatch struct found:\n" + "\n".join(matches)


class TestNoAccumulatedTraceState:
    """TraceState and DebugState must not exist on the VM."""

    def test_no_trace_state(self):
        matches = _grep_rust(r"TraceState", dirs=[VM_CORE_SRC])
        matches = _filter_non_test_non_comment(matches)
        assert matches == [], "TraceState found:\n" + "\n".join(matches)

    def test_no_debug_state_on_vm(self):
        matches = _grep_rust(r"debug.*DebugState|DebugState.*debug", dirs=[VM_CORE_SRC])
        matches = _filter_non_test_non_comment(matches)
        assert matches == [], "DebugState on VM found:\n" + "\n".join(matches)


class TestFiberThreeFields:
    """Fiber should have only: frames, parent, handler."""

    def test_no_pending_fields_on_fiber(self):
        """Fiber must not have pending_* fields."""
        matches = _grep_rust(r"pending_", dirs=[VM_CORE_SRC])
        # Only check segment.rs (Fiber definition)
        matches = [m for m in matches if "segment.rs" in m]
        matches = _filter_non_test_non_comment(matches)
        assert matches == [], "pending_* fields on Fiber:\n" + "\n".join(matches)

    def test_no_interceptor_fields_on_fiber(self):
        """Fiber must not have interceptor_* fields."""
        matches = _grep_rust(r"interceptor_", dirs=[VM_CORE_SRC])
        matches = [m for m in matches if "segment.rs" in m]
        matches = _filter_non_test_non_comment(matches)
        assert matches == [], "interceptor_* fields on Fiber:\n" + "\n".join(matches)
