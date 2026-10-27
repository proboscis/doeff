"""
Main Textual application for doeff-agentic TUI.
"""

from collections.abc import Sequence
from typing import Any, TypeVar

from textual.app import App
from textual.binding import Binding

from doeff import Program, run, with_handlers
from doeff.program import ProgramHandler

from .screens import WorkflowListScreen

_T = TypeVar("_T")


class AgenticTUI(App[None]):
    """Interactive TUI for monitoring and managing agentic workflows.

    The screens never read the state directory or the sessions themselves: they run
    programs of recorded-workflow effects with ``perform``, under the handlers the
    app was made with.
    """

    TITLE = "doeff-agentic"
    CSS = """
    Screen {
        background: $surface;
    }
    """

    BINDINGS = [  # noqa: RUF012 - framework class attribute is intentionally static metadata
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self, handlers: Sequence[ProgramHandler]) -> None:
        super().__init__()
        self._handlers = tuple(handlers)

    def perform(self, program: Program[_T, Any]) -> _T:
        """Run ``program`` under the app's handlers (blocks; screens call it from workers)."""
        return run(with_handlers(self._handlers, program))

    def on_mount(self) -> None:
        """Called when the app is mounted."""
        self.push_screen(WorkflowListScreen())
