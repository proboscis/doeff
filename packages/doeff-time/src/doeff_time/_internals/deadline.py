"""The value a timed wait's deadline completes with."""


class _DeadlinePassed:
    """The deadline of a timed wait passed. Told apart from every future's value by identity (a future may
    complete with None — RaceWithin), so the clock handlers complete their deadline with this, never None."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "DEADLINE_PASSED"


DEADLINE_PASSED = _DeadlinePassed()
