"""
Main Textual application for doeff-agentic TUI.
"""

from textual.app import App
from textual.binding import Binding

from ..api import AgenticAPI
from .screens import WorkflowListScreen


class AgenticTUI(App[None]):
    """Interactive TUI for monitoring and managing agentic workflows."""

    TITLE = "doeff-agentic"
    CSS = """
    Screen {
        background: $surface;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.api = AgenticAPI()

    def on_mount(self) -> None:
        """Called when the app is mounted."""
        self.push_screen(WorkflowListScreen())
