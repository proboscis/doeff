"""Log display handler for slog effects.

This handler intercepts WriterTellEffect and displays structured logs (slog)
to the console using rich, while still accumulating them in the writer log.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from doeff.cesk.frames import ContinueValue, FrameResult
from doeff.effects.writer import WriterTellEffect

if TYPE_CHECKING:
    from doeff.cesk.state import TaskState
    from doeff.cesk.types import Store

# Global console for log output
_console = Console(stderr=True)


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


def handle_tell_with_display(
    effect: WriterTellEffect,
    task_state: TaskState,
    store: Store,
) -> FrameResult:
    """Handle WriterTellEffect with console display for slog messages.
    
    If the message is a dict (structured log), displays it to console using rich.
    Always appends the message to the writer log (normal WriterTellEffect behavior).
    
    Args:
        effect: The WriterTellEffect to handle.
        task_state: Current task state.
        store: Current store.
        
    Returns:
        FrameResult continuing with None value and updated store.
    """
    message = effect.message
    
    # Display structured logs (dicts) to console
    if isinstance(message, dict):
        formatted = format_slog(message)
        _console.print(formatted)
    
    # Normal WriterTellEffect behavior: append to log
    current_log = store.get("__log__", [])
    new_log = list(current_log) + [message]
    new_store = {**store, "__log__": new_log}
    
    return ContinueValue(
        value=None,
        env=task_state.env,
        store=new_store,
        k=task_state.kontinuation,
    )


def log_display_handlers() -> dict[type, Any]:
    """Return handlers for slog display.
    
    Returns:
        Handler dict with WriterTellEffect -> handle_tell_with_display.
        
    Example:
        >>> from doeff import SyncRuntime
        >>> from doeff_preset import log_display_handlers
        >>> 
        >>> runtime = SyncRuntime(handlers=log_display_handlers())
        >>> # slog messages will now display to console
    """
    return {
        WriterTellEffect: handle_tell_with_display,
    }


__all__ = [
    "format_slog",
    "handle_tell_with_display",
    "log_display_handlers",
]
