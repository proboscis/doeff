"""`gc_type_counts` counts the tracked objects by type without holding them.

A census that lists the objects (`gc.get_objects()`) either holds every object
until the list is dropped — which broke asyncio's `StreamReader.readexactly` with
`BufferError` when taken from another thread — or, taken in a forked copy to avoid
that, writes every listed object's reference count and copies the pages that hold
them (2M tracked objects copied 140 MiB locally; a production job's census copied
280 MiB in two seconds). `gc_type_counts` walks the collector's objects in place.
"""

import gc
import subprocess
import sys
import textwrap
from collections import Counter

import pytest
from doeff_vm.doeff_vm import gc_type_counts

pytestmark = pytest.mark.skipif(sys.version_info < (3, 12), reason="PyUnstable_GC_VisitObjects is CPython 3.12+")


class _Tracked:
    """A GC-tracked type no other test creates."""

    def __init__(self, i: int) -> None:
        self.i = i


def test_counts_match_the_population_of_gc_get_objects() -> None:
    keep = [_Tracked(i) for i in range(5000)]
    was = gc.isenabled()
    gc.disable()
    try:
        counted = gc_type_counts()
        listed = Counter(type(o) for o in gc.get_objects() if type(o) is _Tracked)
    finally:
        if was:
            gc.enable()
    assert counted[_Tracked] == listed[_Tracked] == len(keep)
    assert all(isinstance(t, type) and n > 0 for t, n in counted.items())


def test_holds_no_reference_to_the_counted_objects_or_their_types() -> None:
    keep = [_Tracked(i) for i in range(100)]
    before = (sys.getrefcount(keep[0]), sys.getrefcount(_Tracked))
    gc_type_counts()
    assert (sys.getrefcount(keep[0]), sys.getrefcount(_Tracked)) == before


@pytest.mark.tier_medium
def test_frozen_objects_are_not_counted_like_gc_get_objects() -> None:
    """`gc.freeze()` is process-wide, so the check runs in a fresh interpreter."""
    script = textwrap.dedent(
        """
        import gc
        from doeff_vm.doeff_vm import gc_type_counts
        class Row:
            def __init__(self, i): self.i = i
        frozen = [Row(i) for i in range(300)]
        gc.freeze()
        live = [Row(i) for i in range(200)]
        print(gc_type_counts().get(Row, 0), sum(1 for o in gc.get_objects() if type(o) is Row))
        """
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True).stdout.split()
    assert out == ["200", "200"]
