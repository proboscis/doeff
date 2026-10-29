"""
Helper functions for doeff-traverse.
"""

from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, TypeVar

from doeff import do
from doeff_traverse._effects import Fail

if TYPE_CHECKING:
    from doeff import EffectGenerator, Program

_T = TypeVar("_T")


@do
def try_call(f, *args, **kwargs):
    """Wrap a plain Python function call as a yield site.

    If f raises, performs Fail(exception). Handler can Resume(k, value)
    to inject a substitute, or let it propagate as an exception.

    Usage:
        (<- obj (try-call parse-json raw))
    """
    try:
        return f(*args, **kwargs)
    except Exception as e:
        return (yield Fail(e))


def _unchained(chain: tuple) -> Iterator:
    """The values of a chain of ``(value, previous chain)`` pairs, newest first."""
    while chain:
        yield chain[0]
        chain = chain[1]


@do
def sequence(programs: "Iterable[Program[_T]]") -> "EffectGenerator[tuple[_T, ...]]":
    """Run ``programs`` one after another, in the order given, and answer their answers as a tuple.

    No handler is needed and the policy is fixed: sequential, and the first failure stops it.
    A program's exception goes up as it is and the programs after it are never made or run
    (``Traverse`` under ``sequential`` instead records a failed item and reads on, so a caller that
    reads only its valid values loses the failed rows without a word). ``programs`` is read lazily:
    each program is made just before it runs, so a generator is fine. The programs run under the
    caller's handlers. The answers keep their order, duplicates and ``None``.

    The answers are collected in a chain of ``(value, previous chain)`` pairs that is folded into
    the tuple once at the end, so no list is grown and no tuple is copied per program (a tuple
    built by ``+`` per program costs the square of the rows: 3.5 s for 30,000). About 3 µs a
    program, linear to 300,000 programs.

    Usage:
        (<- rows tuple (sequence (gfor #(key value) pairs (reader value))))   ; Hy
        rows = yield sequence(read(value) for value in values)                ; Python
    """
    chain: tuple = ()
    for program in programs:
        value = yield program
        chain = (value, chain)
    return tuple(reversed(tuple(_unchained(chain))))
