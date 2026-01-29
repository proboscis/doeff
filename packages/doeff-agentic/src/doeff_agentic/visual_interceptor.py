"""Visual effect interceptor for doeff-agentic examples."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from rich.console import Console

from doeff import do
from doeff.effects import Intercept
from doeff.effects.writer import WriterTellEffect
from doeff.program import Program

from .effects import (
    AgenticAbortSession,
    AgenticCreateEnvironment,
    AgenticCreateSession,
    AgenticCreateWorkflow,
    AgenticDeleteEnvironment,
    AgenticDeleteSession,
    AgenticForkSession,
    AgenticGetEnvironment,
    AgenticGetMessages,
    AgenticGetSession,
    AgenticGetSessionStatus,
    AgenticGetWorkflow,
    AgenticNextEvent,
    AgenticSendMessage,
    AgenticSupportsCapability,
)

EFFECT_CONFIG: dict[type, dict[str, Any]] = {
    AgenticCreateWorkflow: {"icon": "W+", "color": "blue", "name": "CreateWorkflow"},
    AgenticGetWorkflow: {"icon": "W?", "color": "dim blue", "name": "GetWorkflow"},
    AgenticCreateEnvironment: {"icon": "E+", "color": "green", "name": "CreateEnvironment"},
    AgenticGetEnvironment: {"icon": "E?", "color": "dim green", "name": "GetEnvironment"},
    AgenticDeleteEnvironment: {"icon": "E-", "color": "red", "name": "DeleteEnvironment"},
    AgenticCreateSession: {"icon": "S+", "color": "cyan", "name": "CreateSession"},
    AgenticForkSession: {"icon": "S*", "color": "cyan", "name": "ForkSession"},
    AgenticGetSession: {"icon": "S?", "color": "dim cyan", "name": "GetSession"},
    AgenticAbortSession: {"icon": "S!", "color": "yellow", "name": "AbortSession"},
    AgenticDeleteSession: {"icon": "S-", "color": "red", "name": "DeleteSession"},
    AgenticSendMessage: {"icon": ">>", "color": "magenta", "name": "SendMessage"},
    AgenticGetMessages: {"icon": "<?", "color": "dim magenta", "name": "GetMessages"},
    AgenticNextEvent: {"icon": "~>", "color": "yellow", "name": "NextEvent"},
    AgenticGetSessionStatus: {"icon": "S@", "color": "dim cyan", "name": "GetSessionStatus"},
    AgenticSupportsCapability: {"icon": "??", "color": "dim", "name": "SupportsCapability"},
}


@dataclass
class VisualInterceptorConfig:
    show_timestamps: bool = True
    show_duration: bool = True
    show_slog: bool = True
    show_effect_details: bool = True
    truncate_content: int = 80
    console: Console | None = None


def _get_timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _format_effect_details(effect: Any, config: VisualInterceptorConfig) -> str:
    details: list[str] = []
    effect_fields = getattr(effect, "__dataclass_fields__", {})

    if "name" in effect_fields:
        name = getattr(effect, "name", None)
        if isinstance(name, str) and name:
            details.append(f'name="{name}"')

    if "session_id" in effect_fields:
        session_id = getattr(effect, "session_id", None)
        if isinstance(session_id, str):
            if len(session_id) > 8:
                details.append(f"session={session_id[:8]}...")
            else:
                details.append(f"session={session_id}")

    if "content" in effect_fields:
        content = getattr(effect, "content", None)
        if isinstance(content, str) and content:
            details.append(f'"{_truncate(content, config.truncate_content)}"')

    if "wait" in effect_fields:
        wait = getattr(effect, "wait", None)
        if isinstance(wait, bool):
            details.append(f"wait={wait}")

    if "env_type" in effect_fields:
        env_type = getattr(effect, "env_type", None)
        if env_type is not None and hasattr(env_type, "value"):
            details.append(f"type={env_type.value}")

    if "environment_id" in effect_fields:
        env_id = getattr(effect, "environment_id", None)
        if isinstance(env_id, str) and env_id:
            details.append(f"env={env_id[:8]}...")

    return " ".join(details) if details else ""


def _format_slog(message: dict[str, Any], config: VisualInterceptorConfig) -> str:
    parts: list[str] = []

    if "status" in message:
        parts.append(f"[bold]{message['status']}[/bold]")

    if "msg" in message:
        msg = _truncate(str(message["msg"]), config.truncate_content)
        parts.append(msg)

    for key, value in message.items():
        if key not in ("status", "msg"):
            val_str = _truncate(str(value), 40)
            parts.append(f"{key}={val_str}")

    return " ".join(parts)


def _format_result(result: Any) -> str:
    if result is None:
        return "done"

    if hasattr(result, "id") and hasattr(result, "name"):
        result_id = str(getattr(result, "id", ""))
        result_name = str(getattr(result, "name", ""))
        id_short = result_id[:8] + "..." if len(result_id) > 8 else result_id
        return f"id={id_short} name={result_name}"

    if hasattr(result, "id") and hasattr(result, "session_id"):
        result_id = str(getattr(result, "id", ""))
        id_short = result_id[:12] + "..." if len(result_id) > 12 else result_id
        return f"msg_id={id_short}"

    if isinstance(result, list):
        return f"{len(result)} items"

    if isinstance(result, bool):
        return "yes" if result else "no"

    return _truncate(str(result), 40)


def create_visual_interceptor(
    config: VisualInterceptorConfig | None = None,
) -> tuple[Any, Console]:
    """Create interceptor transform and console for effect visualization."""
    cfg = config or VisualInterceptorConfig()
    console = cfg.console or Console()

    def transform(effect: Any) -> Any:
        effect_type = type(effect)

        if isinstance(effect, WriterTellEffect):
            if cfg.show_slog and isinstance(effect.message, dict):
                timestamp = f"[dim][{_get_timestamp()}][/dim] " if cfg.show_timestamps else ""
                slog_text = _format_slog(effect.message, cfg)
                console.print(f"{timestamp}[yellow]---[/yellow] {slog_text}")
            return None

        effect_config = EFFECT_CONFIG.get(effect_type)
        if effect_config is None:
            return None

        icon = effect_config["icon"]
        color = effect_config["color"]
        name = effect_config["name"]

        timestamp = f"[dim][{_get_timestamp()}][/dim] " if cfg.show_timestamps else ""
        details = _format_effect_details(effect, cfg) if cfg.show_effect_details else ""
        details_str = f" {details}" if details else ""

        console.print(f"{timestamp}[{color}]{icon}[/{color}] [bold]{name}[/bold]{details_str}")

        @do
        def logged_effect():
            start_time = time.time()
            result = yield effect
            elapsed = time.time() - start_time

            if cfg.show_duration:
                duration_str = f" [dim]({elapsed:.1f}s)[/dim]"
            else:
                duration_str = ""

            result_str = _format_result(result)
            console.print(
                f"{timestamp}[dim {color}]<-[/dim {color}] [dim]{result_str}{duration_str}[/dim]"
            )
            return result

        return logged_effect()

    return transform, console


def with_visual_logging(
    program: Program,
    config: VisualInterceptorConfig | None = None,
) -> Program:
    """Wrap a program with visual effect logging for examples and debugging."""
    transform, _ = create_visual_interceptor(config)
    return Intercept(program, transform)  # type: ignore[return-value]


def visual_logging_console(
    config: VisualInterceptorConfig | None = None,
) -> tuple[Any, Console]:
    """Create visual logging wrapper and console for custom usage."""
    transform, console = create_visual_interceptor(config)

    def wrapper(program: Program) -> Program:
        return Intercept(program, transform)  # type: ignore[return-value]

    return wrapper, console


__all__ = [
    "VisualInterceptorConfig",
    "create_visual_interceptor",
    "visual_logging_console",
    "with_visual_logging",
]
