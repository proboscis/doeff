"""The owner that raises the GC threshold only during the outermost load (running a module body -- including imports inside it).

The CPython 3.14 free-threaded GC sweeps the whole heap when the net number of objects since the previous GC grows past the threshold (default 2000), at least 1/4 of them survive, and RSS
grows more than 10%. Loading grows the heap with long-lived objects (modules, functions, types), so even with about 1% garbage it sweeps every time RSS
grows 10% -- one load of agora-controllers' test_emulated_screen.hy (about 2 seconds) did 7 sweeps, 0.28 to 0.38 seconds.

The wrapper of the reader entry (:mod:`doeff_hy_bytecode_guard._loader_hooks`) raises the threshold with :func:`hold` to
:data:`IMPORT_GC_THRESHOLD` during the outermost module body run and restores it with :func:`release` at the end. It uses a finite value rather than stopping (gc.disable) because the threshold is also an upper bound on "the number of
garbage objects that can pile up since the previous GC" -- GC still happens during loading, and the garbage that piles up is capped at this number (stopping it
raised max RSS by 11 MB in test_emulated_screen.hy, and the GC for the piled-up amount moved into the test body).

The threshold is one per process, so it stays raised while any thread's load continues, and is restored when the last load ends. If a module read in the middle of a load
sets the threshold itself (differs from the raised value), that value is kept. Threshold 0 (automatic GC turned off) is left alone.
A child forked in the middle of another thread's load does not have that thread and the load does not end in the child, so only the forking thread's share is kept.

It is imported from the wrapper read at startup, so it imports only light standard-library modules (it does not even read threading).
"""

import gc
import os
from _thread import allocate_lock, get_ident

#: The threshold during the outermost load (the number of objects that may grow net since the previous GC). Comparing 150,000 / 200,000 /
#: 250,000 / 400,000 on loading test_emulated_screen.hy, this was the value where GC during loading went from 7 to 2, GC was not moved into the test body, and max RSS was within noise.
IMPORT_GC_THRESHOLD = 250_000


class _ImportHold:
    """Memo while the threshold is raised (one per process -- this module is the only owner)."""

    __slots__ = ("before", "holders", "lock")

    def __init__(self) -> None:
        self.lock = allocate_lock()
        #: Thread id of a loading thread -> the number of outermost loads of that thread (even on the same thread, outermost loads can overlap if the context differs).
        #: Held per thread so a forked child can keep only the forking thread's share.
        self.holders: dict[int, int] = {}
        #: The threshold before raising (None if not raised).
        self.before: int | None = None


_HOLD = _ImportHold()


def hold() -> None:
    """Start of an outermost load -- to reduce GC during the load, raise the threshold if no other thread is loading."""
    with _HOLD.lock:
        ident = get_ident()
        first = not _HOLD.holders
        _HOLD.holders[ident] = _HOLD.holders.get(ident, 0) + 1
        if first:
            _raise_threshold()


def release() -> None:
    """End of an outermost load (even on an exception) -- to return GC outside loading to the default, restore the threshold when no thread is loading."""
    with _HOLD.lock:
        ident = get_ident()
        left = _HOLD.holders.get(ident, 0) - 1
        if left > 0:
            _HOLD.holders[ident] = left
        else:
            _HOLD.holders.pop(ident, None)
        if not _HOLD.holders:
            _restore_threshold()


def _raise_threshold() -> None:
    """Raise the threshold to the value for loading and remember the previous value (GC that is turned off or an already high threshold is left alone as the user's decision)."""
    current = gc.get_threshold()
    if current[0] == 0 or current[0] >= IMPORT_GC_THRESHOLD:
        _HOLD.before = None
        return
    _HOLD.before = current[0]
    gc.set_threshold(IMPORT_GC_THRESHOLD, *current[1:])


def _restore_threshold() -> None:
    """Restore the remembered previous threshold -- if a loaded module set the threshold itself (differs from the raised value), keep that decision."""
    before, _HOLD.before = _HOLD.before, None
    current = gc.get_threshold()
    if before is not None and current[0] == IMPORT_GC_THRESHOLD:
        gc.set_threshold(before, *current[1:])


def _after_fork_in_child() -> None:
    """In a forked child, drop the loads of threads that do not exist in the child -- those loads never end in the child, so the threshold would stay raised.
    The forking thread's own load ends in the child, so it is kept. The lock is recreated because another thread may have held it at fork time."""
    _HOLD.lock = allocate_lock()
    ident = get_ident()
    mine = _HOLD.holders.get(ident, 0)
    _HOLD.holders = {ident: mine} if mine else {}
    if not mine:
        _restore_threshold()


os.register_at_fork(after_in_child=_after_fork_in_child)
