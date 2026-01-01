"""CLI for doeff-agents."""

import sys
import time
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from .adapters.base import AgentType, LaunchConfig
from .monitor import SessionStatus
from .session import (
    AgentSession,
    capture_output,
    launch_session,
    monitor_session,
    send_message,
)
from .tmux import attach_session as tmux_attach
from .tmux import has_session, kill_session, list_sessions

console = Console()

# All doeff-agents sessions are prefixed with this to distinguish from other tmux sessions
SESSION_PREFIX = "doeff-"


def _to_tmux_name(user_name: str) -> str:
    """Convert user-provided name to tmux session name (add prefix if needed)."""
    if user_name.startswith(SESSION_PREFIX):
        return user_name
    return f"{SESSION_PREFIX}{user_name}"


def _to_display_name(tmux_name: str) -> str:
    """Convert tmux session name to display name (strip prefix)."""
    if tmux_name.startswith(SESSION_PREFIX):
        return tmux_name[len(SESSION_PREFIX):]
    return tmux_name


def _resolve_session(user_name: str) -> str | None:
    """Resolve user-provided name to actual tmux session name.

    Tries prefixed name first, then exact name for backwards compatibility.
    Returns None if session not found.
    """
    prefixed = _to_tmux_name(user_name)
    if has_session(prefixed):
        return prefixed
    # Fallback: try exact name (for manually created sessions)
    if has_session(user_name):
        return user_name
    return None


@click.group()
def cli() -> None:
    """doeff-agents: Agent session management for coding agents."""


@cli.command()
@click.option(
    "--agent",
    "-a",
    type=click.Choice(["claude", "codex", "gemini"]),
    default="claude",
    help="Agent type to use",
)
@click.option(
    "--work-dir",
    "-w",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
    help="Working directory for the agent",
)
@click.option("--prompt", "-p", type=str, help="Initial prompt for the agent")
@click.option("--profile", type=str, help="Agent profile to use")
@click.option("--session", "-s", type=str, help="Session name (auto-generated if not provided)")
@click.option("--watch", "-W", is_flag=True, help="Watch session after launching")
def run(
    agent: str,
    work_dir: Path,
    prompt: str | None,
    profile: str | None,
    session: str | None,
    watch: bool,
) -> None:
    """Launch a new agent session."""
    agent_type = AgentType(agent)

    # Generate session name if not provided
    if not session:
        import uuid

        display_name = f"{agent}-{uuid.uuid4().hex[:8]}"
    else:
        display_name = session

    # Always use prefixed name for tmux
    tmux_name = _to_tmux_name(display_name)

    config = LaunchConfig(
        agent_type=agent_type,
        work_dir=work_dir.absolute(),
        prompt=prompt,
        profile=profile,
    )

    try:
        agent_session = launch_session(tmux_name, config)
        console.print(f"[green]✓[/green] Launched session: [bold]{display_name}[/bold]")
        console.print(f"  Agent: {agent}")
        console.print(f"  Work dir: {work_dir.absolute()}")
        console.print(f"  Pane ID: {agent_session.pane_id}")

        if watch:
            console.print("\nWatching session (Ctrl+C to stop)...")
            _watch_session(agent_session)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@cli.command("ps")
@click.option("--all", "-a", "show_all", is_flag=True, help="Show all tmux sessions, not just doeff-agents")
def ps_command(show_all: bool) -> None:
    """List doeff-agents sessions."""
    try:
        sessions = list_sessions()
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    # Filter to only doeff sessions unless --all is specified
    if not show_all:
        sessions = [s for s in sessions if s.startswith(SESSION_PREFIX)]

    if not sessions:
        if show_all:
            console.print("No tmux sessions found.")
        else:
            console.print("No doeff-agents sessions found. Use --all to see all tmux sessions.")
        return

    table = Table(title="Agent Sessions" if not show_all else "All Tmux Sessions")
    table.add_column("Session Name", style="cyan")
    if show_all:
        table.add_column("Type", style="green")

    for tmux_name in sessions:
        if show_all:
            is_doeff = tmux_name.startswith(SESSION_PREFIX)
            type_str = "[blue]doeff-agents[/blue]" if is_doeff else "[dim]other[/dim]"
            display = _to_display_name(tmux_name) if is_doeff else tmux_name
            table.add_row(display, type_str)
        else:
            # Show display name (without prefix)
            table.add_row(_to_display_name(tmux_name))

    console.print(table)


@cli.command()
@click.argument("session_name")
def attach(session_name: str) -> None:
    """Attach to a tmux session."""
    from .tmux import is_inside_tmux

    tmux_name = _resolve_session(session_name)
    if tmux_name is None:
        console.print(f"[red]Error:[/red] Session '{session_name}' not found")
        sys.exit(1)

    display_name = _to_display_name(tmux_name)
    if is_inside_tmux():
        console.print(f"Switching to session: {display_name}")
    else:
        console.print(f"Attaching to session: {display_name}")
        console.print("Press Ctrl+B, D to detach")
    tmux_attach(tmux_name)


@cli.command()
@click.argument("session_name")
def stop(session_name: str) -> None:
    """Stop (kill) a tmux session."""
    tmux_name = _resolve_session(session_name)
    if tmux_name is None:
        console.print(f"[red]Error:[/red] Session '{session_name}' not found")
        sys.exit(1)

    kill_session(tmux_name)
    console.print(f"[green]✓[/green] Stopped session: {_to_display_name(tmux_name)}")


@cli.command("watch")
@click.argument("session_name")
@click.option("--interval", "-i", type=float, default=1.0, help="Poll interval in seconds")
def watch_command(session_name: str, interval: float) -> None:
    """Monitor a running session."""
    tmux_name = _resolve_session(session_name)
    if tmux_name is None:
        console.print(f"[red]Error:[/red] Session '{session_name}' not found")
        sys.exit(1)

    # Create a minimal AgentSession for monitoring
    session = AgentSession(
        session_name=tmux_name,
        pane_id=tmux_name,  # Use session name as pane target
        agent_type=AgentType.CLAUDE,  # Default, will work for monitoring
        work_dir=Path.cwd(),
    )

    display_name = _to_display_name(tmux_name)
    console.print(f"Watching session: {display_name} (Ctrl+C to stop)")
    _watch_session(session, poll_interval=interval)


def _watch_session(session: AgentSession, poll_interval: float = 1.0) -> None:
    """Watch a session and display status updates."""

    def on_status_change(old: SessionStatus, new: SessionStatus, output: str | None) -> None:
        status_colors = {
            SessionStatus.PENDING: "dim",
            SessionStatus.BOOTING: "yellow",
            SessionStatus.RUNNING: "green",
            SessionStatus.BLOCKED: "cyan",
            SessionStatus.BLOCKED_API: "red",
            SessionStatus.DONE: "green bold",
            SessionStatus.FAILED: "red bold",
            SessionStatus.EXITED: "dim",
            SessionStatus.STOPPED: "dim",
        }
        color = status_colors.get(new, "white")
        console.print(f"Status: [{color}]{new.value}[/{color}]")

    def on_pr_detected(url: str) -> None:
        console.print(f"[green]PR Created:[/green] {url}")

    try:
        while not session.is_terminal:
            monitor_session(
                session,
                on_status_change=on_status_change,
                on_pr_detected=on_pr_detected,
            )
            time.sleep(poll_interval)

        console.print(f"\nSession ended with status: [bold]{session.status.value}[/bold]")
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped watching (session still running)[/dim]")


@cli.command()
@click.argument("session_name")
@click.argument("message")
def send(session_name: str, message: str) -> None:
    """Send a message to a running session."""
    tmux_name = _resolve_session(session_name)
    if tmux_name is None:
        console.print(f"[red]Error:[/red] Session '{session_name}' not found")
        sys.exit(1)

    session = AgentSession(
        session_name=tmux_name,
        pane_id=tmux_name,
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
    )

    send_message(session, message)
    console.print(f"[green]✓[/green] Sent message to {_to_display_name(tmux_name)}")


@cli.command()
@click.argument("session_name")
@click.option("--lines", "-n", type=int, default=50, help="Number of lines to capture")
def output(session_name: str, lines: int) -> None:
    """Capture output from a session."""
    tmux_name = _resolve_session(session_name)
    if tmux_name is None:
        console.print(f"[red]Error:[/red] Session '{session_name}' not found")
        sys.exit(1)

    session = AgentSession(
        session_name=tmux_name,
        pane_id=tmux_name,
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
    )

    output_text = capture_output(session, lines)
    console.print(output_text)


if __name__ == "__main__":
    cli()
