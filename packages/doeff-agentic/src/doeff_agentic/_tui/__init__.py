"""
Textual TUI for doeff-agentic workflow monitoring and interaction.

This module provides an interactive terminal UI for humans to monitor
and interact with agentic workflows. The screens read and act on the recorded
workflows with the effects of ``doeff_agentic._effects.recorded``; ``main`` is the
composition root that runs them under ``recorded_workflows_handler``.

Usage:
    $ doeff-agentic-tui

    or

    $ doeff-agentic tui
"""

from doeff_agentic._handlers.recorded import recorded_workflows_handler

from .app import AgenticTUI


def main() -> None:
    """Entry point for the TUI application."""
    app = AgenticTUI(handlers=(recorded_workflows_handler(),))
    app.run()


__all__ = ["AgenticTUI", "main"]
