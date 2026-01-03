"""
Textual TUI for doeff-agentic workflow monitoring and interaction.

This module provides an interactive terminal UI for humans to monitor
and interact with agentic workflows.

Usage:
    $ doeff-agentic-tui

    or

    $ doeff-agentic tui
"""

from .app import AgenticTUI


def main() -> None:
    """Entry point for the TUI application."""
    app = AgenticTUI()
    app.run()


__all__ = ["AgenticTUI", "main"]
