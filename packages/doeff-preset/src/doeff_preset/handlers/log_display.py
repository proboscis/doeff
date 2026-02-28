"""Log display handler for slog effects.

This handler intercepts WriterTellEffect and displays structured logs (slog)
to the console using rich, while still accumulating them in the writer log.
"""


from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from doeff import Effect, Pass, WriterTellEffect, do

# Global console for log output
_console = Console(stderr=True)
ProtocolHandler = Callable[[Any, Any], Any]


def format_slog(message: dict[str, Any]) -> Panel | Text:
    """Format a structured log message for rich display.

    Args:
        message: The slog payload dictionary.

    Returns:
        A rich renderable (Panel or Text) for console output.
    """
    # Extract common fields
    level = str(message.get("level", "info")).lower()
    msg = message.get("msg", message.get("message", ""))
    step = message.get("step", "")
    status = message.get("status", "")

    # Build display text
    parts: list[str] = []

    if step:
        parts.append(f"[bold cyan]{step}[/bold cyan]")
    if status:
        parts.append(f"[bold magenta]{status}[/bold magenta]")
    if msg:
        parts.append(str(msg))

    # Add remaining fields (excluding already processed ones)
    processed = {"level", "msg", "message", "step", "status"}
    extras = {k: v for k, v in message.items() if k not in processed}
    if extras:
        extra_strs = [f"[dim]{k}=[/dim]{v}" for k, v in extras.items()]
        parts.append(" ".join(extra_strs))

    display_text = " | ".join(parts) if parts else str(message)

    # Color based on level
    level_colors = {
        "debug": "dim",
        "info": "blue",
        "warning": "yellow",
        "warn": "yellow",
        "error": "red",
        "critical": "bold red",
    }
    color = level_colors.get(level, "blue")

    level_badge = f"[{color}]{level.upper():>8}[/{color}]"

    return Text.from_markup(f"{level_badge} {display_text}")


@do
def handle_tell_with_display(
    effect: Effect,
    _k: Any,
):
    """Handle WriterTellEffect with console display for slog messages.

    If the message is a dict (structured log), displays it to console using rich.
    Always appends the message to the writer log (normal WriterTellEffect behavior).

    Args:
        effect: The WriterTellEffect to handle.
        ctx: Handler context containing task_state and store.

    Returns:
        Pass-through to the outer Writer handler after optional display.
    """
    if not isinstance(effect, WriterTellEffect):
        yield Pass()
        return None

    message = effect.message

    # Display structured logs (dicts) to console
    if isinstance(message, dict):
        formatted = format_slog(message)
        _console.print(formatted)

    # Delegate to outer Writer handler for normal log accumulation.
    yield Pass()
    return None


def log_display_handlers() -> ProtocolHandler:
    """Return a protocol handler for slog display."""

    @do
    def handler(effect: Effect, k: Any):
        return (yield handle_tell_with_display(effect, k))

    return handler


__all__ = [
    "ProtocolHandler",
    "format_slog",
    "handle_tell_with_display",
    "log_display_handlers",
]
