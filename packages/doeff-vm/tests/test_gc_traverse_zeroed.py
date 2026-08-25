"""`tp_traverse` must tolerate a pyclass instance whose Rust contents are still zeroed.

Regression guard for the agora-1 pod SIGSEGV of 2026-08-26: every `ai usage`
observation inside the pod died with rc=-11 while the interpreter was
garbage-collecting, so seat placement ran on `basis:unobserved`.

Mechanism: CPython GC-tracks a new instance inside `tp_alloc`, while pyo3
writes the Rust value into it only after the base `tp_new` returns. A cycle
collection in that window calls `tp_traverse` on all-zero fields. `Py<T>` is
`NonNull`, so the null guard inside `PyVisit::call` is folded away by
optimized builds and CPython's `visit_decref` dereferences `Py_TYPE(NULL)`.

The first test is the deterministic oracle (it holds on every interpreter and
build profile). The second is the end-to-end reproducer, which only actually
crashes on the reference-counted GC of CPython <= 3.13 in a release build.
"""

import subprocess
import sys
import textwrap

import doeff_vm
import pytest
from doeff_vm.doeff_vm import gc_traverse_zeroed_visits

_EXTENSION_MODULE = "doeff_vm.doeff_vm"


def _extension_types() -> list[str]:
    names = []
    for name in sorted(dir(doeff_vm)):
        obj = getattr(doeff_vm, name)
        if isinstance(obj, type) and getattr(obj, "__module__", None) == _EXTENSION_MODULE:
            names.append(name)
    return names


def test_extension_types_are_discoverable() -> None:
    """Guard the guard: the parametrisation below must not silently cover nothing."""
    assert len(_extension_types()) >= 15


@pytest.mark.parametrize("name", _extension_types())
def test_traverse_of_zeroed_instance_never_visits_null(name: str) -> None:
    cls = getattr(doeff_vm, name)
    visits, nulls = gc_traverse_zeroed_visits(cls)
    assert nulls == 0, (
        f"{name}.__traverse__ handed {nulls}/{visits} null pointers to the visitproc "
        "when the instance's Rust contents were still zeroed. CPython's visit_decref "
        "dereferences Py_TYPE(NULL) on such a pointer and the interpreter segfaults. "
        "Visit Py fields through doeff_vm::gc::visit_py_field."
    )


_STRESS = textwrap.dedent(
    """
    import gc
    import doeff_vm as V

    # Collect on essentially every allocation so the alloc/init window of each
    # construction is actually observed by the cycle collector.
    gc.set_threshold(1, 1, 1)

    kept = []
    for i in range(20000):
        kept.append(V.Pure(i))
        V.Perform(i)
        V.Expand(i)
        V.Apply(V.Pure(i), [])
        V.Ok(i)
        V.Callable(lambda: i)
        if len(kept) > 500:
            kept.clear()
    print("ok")
    """
)


def test_construction_under_aggressive_gc_does_not_crash() -> None:
    proc = subprocess.run(
        [sys.executable, "-c", _STRESS],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert proc.returncode == 0, (
        f"constructing doeff_vm program nodes under an aggressive GC exited "
        f"{proc.returncode} (negative = fatal signal; -11 is the pod SIGSEGV).\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert proc.stdout.strip().endswith("ok")
