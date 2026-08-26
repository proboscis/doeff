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

import doeff_vm.doeff_vm as _extension
import pytest
from doeff_vm.doeff_vm import gc_traverse_zeroed_visits


def _extension_types() -> list[str]:
    """Every type the extension module defines.

    The population is read off the extension module itself, never selected by a
    ``__module__`` string: a ``#[pyclass]`` written without ``module = "..."``
    reports ``__module__ == "builtins"`` (``K``, ``PyVM`` do today) and an
    exception minted by ``create_exception!`` reports the package
    (``UnhandledEffect``). A string filter therefore drops exactly the classes
    nobody remembered to annotate — the same ones most likely to have been
    written without the null guard.

    ``doeff_vm.doeff_vm`` is also the right module to read from:
    ``doeff_vm/__init__.py`` is shared by every ABI-tagged ``.so`` in the tree,
    while the extension module is version-locked to the oracle imported above
    (see the ``gc_traverse_zeroed_visits`` note in packages/doeff-vm/src/lib.rs).
    """
    return sorted(name for name in dir(_extension) if isinstance(getattr(_extension, name), type))


def test_extension_types_are_discoverable() -> None:
    """Guard the guard: the parametrisation below must not silently cover nothing."""
    assert len(_extension_types()) >= 15


def test_population_covers_every_type_the_extension_defines() -> None:
    """Guard the guard, second axis: the population must not be a `__module__` filter.

    A `#[pyclass]` declared without `module = "..."` reports
    `__module__ == "builtins"`, and an exception minted by `create_exception!`
    reports the package name — so selecting types by `__module__` string silently
    drops them from the parametrisation while the count assertion above still
    passes. The population must be read off the extension module itself.
    """
    defined = {name for name in dir(_extension) if isinstance(getattr(_extension, name), type)}
    covered = set(_extension_types())
    assert defined - covered == set(), (
        "these extension types are never handed to the traverse oracle: "
        f"{sorted(defined - covered)}. Their tp_traverse is unchecked, so the "
        "null-visit defect can return through them unnoticed."
    )


@pytest.mark.parametrize("name", _extension_types())
def test_traverse_of_zeroed_instance_never_visits_null(name: str) -> None:
    cls = getattr(_extension, name)
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
