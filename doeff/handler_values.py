"""Handlers carried as values, declared by the factories that make them.

A program sometimes receives the handlers it places as a value — in a parameter, in a field of an
effect's answer or of a record — instead of writing the factory call in its ``with_handlers`` list:
the value is made once by the composition (a client per process) and carried to where it is placed.
A static reader (doeff-effect-analyzer) cannot know such a value. The type of the place says which
factories make it::

    NoticeReceiver = Annotated[Callable[..., object], BuiltBy(notice_events_handler)]

    @dataclass(frozen=True)
    class RowEvents:
        handlers: tuple[NoticeReceiver, ...]

``BuiltBy(*builders)`` names the factories (or handler-list builders) whose handlers the value holds,
outermost first. The reader reads a list placed from a place of that type as the handlers those
factories make (their clauses or their ``__doeff_handles__`` / ``__doeff_effects__`` declarations).
At run time the mark adds nothing: ``Annotated`` is the type it annotates.
"""

from dataclasses import dataclass
from collections.abc import Callable


@dataclass(frozen=True, init=False)
class BuiltBy:
    """The factories whose handlers a value of the annotated type holds (outermost first)."""

    builders: tuple[Callable[..., object], ...]

    def __init__(self, *builders: Callable[..., object]) -> None:
        if not builders:
            raise ValueError("BuiltBy needs at least one factory")
        object.__setattr__(self, "builders", builders)

    @property
    def __doeff_built_by__(self) -> tuple[Callable[..., object], ...]:
        """What doeff-effect-analyzer reads (duck-typed — the reader does not import this module)."""
        return self.builders
