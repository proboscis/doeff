"""CLI for doeff-agents."""

import json
import sys
import time
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from doeff_agents.agentd_client import (
    AgentdClient,
    AgentdClientError,
    AgentdSessionParseWarning,
    AgentdUnavailableError,
    default_agentd_paths,
    ensure_agentd,
)
from doeff_agents.effects import AgentSessionSnapshot

from .adapters.base import AgentType, LaunchConfig
from .monitor import SessionStatus
from .session import (
    AgentSession,
    launch_session,
    monitor_session,
)
from .tmux import attach_session as tmux_attach
from .tmux import has_session, kill_session

console = Console()

# All doeff-agents sessions are prefixed with this to distinguish from other tmux sessions
SESSION_PREFIX = "doeff-"
AGENTD_UNAVAILABLE_HINT = "agentd が起動していません。doeff-agents agentd ensure(または doeff-sessionhost ... serve)を実行してください。"
TERMINAL_STATUSES = {
    SessionStatus.DONE,
    SessionStatus.FAILED,
    SessionStatus.EXITED,
    SessionStatus.STOPPED,
}


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


def _agentd_client_or_exit() -> AgentdClient:
    try:
        return ensure_agentd()
    except AgentdUnavailableError as error:
        _print_agentd_unavailable(error)
        sys.exit(1)


def _print_agentd_unavailable(error: AgentdUnavailableError) -> None:
    console.print(f"[red]Error:[/red] {AGENTD_UNAVAILABLE_HINT}")
    console.print(f"[dim]{error}[/dim]")


def _print_agentd_request_error(error: AgentdClientError | OSError) -> None:
    if isinstance(error, OSError):
        console.print(f"[red]Error:[/red] {AGENTD_UNAVAILABLE_HINT}")
        console.print(f"[dim]{error}[/dim]")
        return
    console.print(f"[red]Error:[/red] {error}")


def _print_agentd_parse_warnings(warnings: tuple[AgentdSessionParseWarning, ...]) -> None:
    for warning in warnings:
        click.echo(
            "Warning: Skipping unparseable agentd session "
            f"{warning.session_name}: {warning.field}={warning.raw_value!r}",
            err=True,
        )


def _resolve_agentd_session(
    client: AgentdClient,
    user_name: str,
) -> AgentSessionSnapshot | None:
    """Resolve a user-provided session id/name through agentd only."""
    candidate_ids = _agentd_session_id_candidates(user_name)
    for session_id in candidate_ids:
        snapshot = client.get_session(session_id)
        if snapshot is not None:
            return snapshot

    snapshots = client.list_sessions()
    matches = [
        snapshot
        for snapshot in snapshots
        if _agentd_snapshot_matches_name(snapshot, user_name, candidate_ids)
    ]
    if len(matches) > 1:
        names = ", ".join(snapshot.session_id for snapshot in matches)
        raise AgentdClientError(
            f"Session name '{user_name}' is ambiguous in agentd: {names}"
        )
    if len(matches) == 1:
        return matches[0]
    return None


def _agentd_session_id_candidates(user_name: str) -> tuple[str, ...]:
    prefixed_name = _to_tmux_name(user_name)
    candidates = [user_name]
    if prefixed_name != user_name:
        candidates.append(prefixed_name)
    return tuple(candidates)


def _agentd_snapshot_matches_name(
    snapshot: AgentSessionSnapshot,
    user_name: str,
    candidate_ids: tuple[str, ...],
) -> bool:
    return (
        snapshot.session_id in candidate_ids
        or snapshot.session_name in candidate_ids
        or _to_display_name(snapshot.session_name) == user_name
    )


def _agentd_session_or_exit(client: AgentdClient, user_name: str) -> AgentSessionSnapshot:
    try:
        snapshot = _resolve_agentd_session(client, user_name)
    except (AgentdClientError, OSError) as error:
        _print_agentd_request_error(error)
        sys.exit(1)

    if snapshot is None:
        console.print(f"[red]Error:[/red] Session '{user_name}' not found in agentd")
        sys.exit(1)
    return snapshot


def _agentd_display_name(snapshot: AgentSessionSnapshot) -> str:
    return _to_display_name(snapshot.session_name)


def _is_terminal_snapshot(snapshot: AgentSessionSnapshot) -> bool:
    return snapshot.status in TERMINAL_STATUSES


def _resolve_tmux_session(user_name: str) -> str | None:
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


@cli.group()
def agentd() -> None:
    """Manage the doeff-agentd supervisor."""


@agentd.command("kinds")
@click.option("--json", "json_output", is_flag=True, help="Print machine-readable kinds.")
def agentd_kinds(json_output: bool) -> None:
    """Print the running host's advertised binding-kind vocabulary.

    Deliberately does NOT ensure/spawn the daemon: kind verification is a
    read-only observation and must never couple to host liveness (an
    unreachable host means "no observation", not "start a host").
    """
    paths = default_agentd_paths()
    client = AgentdClient(paths.socket_path, timeout=5.0)
    try:
        rows = client.kinds()
    except (AgentdClientError, OSError) as error:
        _print_agentd_request_error(error)
        sys.exit(1)

    payload = {"socket_path": str(paths.socket_path), "kinds": [dict(r) for r in rows]}
    if json_output:
        click.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        for row in payload["kinds"]:
            click.echo(
                f"{row['kind']} -> agent_type={row.get('agent_type')} "
                f"required_field={row.get('required_field')} "
                f"api_version={row.get('api_version')}"
            )


@agentd.command("ensure")
@click.option("--json", "json_output", is_flag=True, help="Print machine-readable status.")
def agentd_ensure(json_output: bool) -> None:
    """Ensure doeff-agentd is reachable, starting it if necessary."""
    try:
        client = ensure_agentd()
    except AgentdUnavailableError as error:
        _print_agentd_unavailable(error)
        sys.exit(1)

    try:
        status = dict(client.status())
    except (AgentdClientError, OSError) as error:
        _print_agentd_request_error(error)
        sys.exit(1)

    payload = {
        "socket_path": str(client.socket_path),
        "status": status,
    }
    daemon_db = status.get("db_path")
    if isinstance(daemon_db, str):
        payload["db_path"] = daemon_db

    if json_output:
        click.echo(json.dumps(payload, sort_keys=True))
        return

    console.print(f"doeff-agentd: {status.get('state', 'running')}")
    console.print(f"socket: {client.socket_path}")
    if isinstance(daemon_db, str):
        console.print(f"db: {daemon_db}")


def _request_or_exit(client: AgentdClient, method: str, params: dict) -> object:
    try:
        return client.request(method, params)
    except (AgentdClientError, OSError) as error:
        _print_agentd_request_error(error)
        sys.exit(1)


@agentd.command("adopt")
@click.option("--session-name", required=True, help="Live mux session / herdr agent name.")
@click.option(
    "--substrate-kind",
    required=True,
    type=click.Choice(["tmux", "herdr"]),
    help="Substrate binding kind (must match the host backend).",
)
@click.option("--substrate-ref", required=True, help="Substrate-native pane reference.")
@click.option("--agent-kind", required=True, help="Agent vocabulary passthrough (claude/codex/...).")
@click.option("--lifecycle", default="interactive", show_default=True)
@click.option("--name", "display_name", default=None, help="Human-readable name.")
@click.option("--work-dir", default=None)
def agentd_adopt(
    session_name: str,
    substrate_kind: str,
    substrate_ref: str,
    agent_kind: str,
    lifecycle: str,
    display_name: str | None,
    work_dir: str | None,
) -> None:
    """Register an ALREADY-LIVE seat in the session ledger (koine
    session.adopt — observation-only; existence is verified before
    registration, a failed adopt leaves no row)."""
    client = _agentd_client_or_exit()
    params: dict = {
        "session_name": session_name,
        "substrate": {"kind": substrate_kind, "ref": substrate_ref},
        "agent_kind": agent_kind,
        "lifecycle": lifecycle,
    }
    if display_name is not None:
        params["name"] = display_name
    if work_dir is not None:
        params["work_dir"] = work_dir
    result = _request_or_exit(client, "session.adopt", params)
    click.echo(json.dumps(result, ensure_ascii=False, sort_keys=True))


def _turn_descriptor(pane_id: str | None, agent_name: str | None) -> dict:
    if pane_id is None and agent_name is None:
        console.print("[red]Error:[/red] provide --pane-id and/or --agent-name")
        sys.exit(1)
    descriptor: dict = {}
    if pane_id is not None:
        descriptor["pane_id"] = pane_id
    if agent_name is not None:
        descriptor["agent_name"] = agent_name
    return descriptor


@agentd.command("turn-open")
@click.option("--pane-id", default=None, help="Descriptor first key (e.g. HERDR_PANE_ID).")
@click.option("--agent-name", default=None, help="Descriptor second key (seat/agent name).")
def agentd_turn_open(pane_id: str | None, agent_name: str | None) -> None:
    """Stamp turn-open (holder=agent) on the adopted seat resolved from the
    descriptor. Ops/debug binding — the seat's hook hot path writes the raw
    socket line instead (<=200ms fire-and-forget; a CLI start-up would
    break that budget)."""
    client = _agentd_client_or_exit()
    result = _request_or_exit(
        client,
        "session.turn_open",
        {"descriptor": _turn_descriptor(pane_id, agent_name)},
    )
    click.echo(json.dumps(result, ensure_ascii=False, sort_keys=True))


@agentd.command("turn-close")
@click.option("--pane-id", default=None, help="Descriptor first key (e.g. HERDR_PANE_ID).")
@click.option("--agent-name", default=None, help="Descriptor second key (seat/agent name).")
@click.option("--wait-who", default=None, help="WAIT destination (user/work/...).")
@click.option("--wait-kind", default=None, help="WAIT kind passthrough (decide/review/...).")
@click.option("--wait-reason", default=None, help="WAIT reason passthrough.")
def agentd_turn_close(
    pane_id: str | None,
    agent_name: str | None,
    wait_who: str | None,
    wait_kind: str | None,
    wait_reason: str | None,
) -> None:
    """Stamp turn-close on the adopted seat resolved from the descriptor.
    holder becomes wait.who (or "work" without a wait); the wait fields are
    stored opaquely — the parse authority stays with the seat's wait
    protocol."""
    client = _agentd_client_or_exit()
    params: dict = {"descriptor": _turn_descriptor(pane_id, agent_name)}
    wait: dict = {}
    if wait_who is not None:
        wait["who"] = wait_who
    if wait_kind is not None:
        wait["kind"] = wait_kind
    if wait_reason is not None:
        wait["reason"] = wait_reason
    if wait:
        params["wait"] = wait
    result = _request_or_exit(client, "session.turn_close", params)
    click.echo(json.dumps(result, ensure_ascii=False, sort_keys=True))


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
@click.option("--model", "-m", type=str, help="Model to use")
@click.option("--session", "-s", type=str, help="Session name (auto-generated if not provided)")
@click.option("--watch", "-W", is_flag=True, help="Watch session after launching")
def run(
    agent: str,
    work_dir: Path,
    prompt: str | None,
    model: str | None,
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
        model=model,
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
@click.option(
    "--all",
    "-a",
    "show_all",
    is_flag=True,
    help="Show all agentd sessions (kept for CLI compatibility)",
)
def ps_command(show_all: bool) -> None:
    """List doeff-agents sessions."""
    client = _agentd_client_or_exit()
    try:
        session_list = client.list_sessions_with_warnings()
    except (AgentdClientError, OSError) as error:
        _print_agentd_request_error(error)
        sys.exit(1)

    _print_agentd_parse_warnings(session_list.warnings)
    sessions = session_list.snapshots

    if not sessions:
        console.print("No agentd sessions found.")
        return

    table = Table(title="Agentd Sessions" if show_all else "Agent Sessions")
    table.add_column("Session ID", style="cyan")
    table.add_column("Name", style="cyan")
    table.add_column("Agent", style="green")
    table.add_column("Status", style="yellow")
    table.add_column("Work Dir", style="dim")

    for snapshot in sessions:
        table.add_row(
            snapshot.session_id,
            _agentd_display_name(snapshot),
            snapshot.agent_type.value,
            snapshot.status.value,
            str(snapshot.work_dir),
        )

    console.print(table)


@cli.command()
@click.argument("session_name")
def attach(session_name: str) -> None:
    """Attach to a tmux session."""
    from .tmux import is_inside_tmux

    client = _agentd_client_or_exit()
    snapshot = _agentd_session_or_exit(client, session_name)
    if snapshot.backend_kind != "tmux":
        console.print(
            f"[red]Error:[/red] Session '{session_name}' is not backed by tmux "
            f"(backend: {snapshot.backend_kind})"
        )
        sys.exit(1)

    tmux_name = snapshot.session_name
    display_name = _agentd_display_name(snapshot)
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
    tmux_name = _resolve_tmux_session(session_name)
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
    client = _agentd_client_or_exit()
    snapshot = _agentd_session_or_exit(client, session_name)
    display_name = _agentd_display_name(snapshot)
    console.print(f"Watching session: {display_name} (Ctrl+C to stop)")
    _watch_agentd_session(client, snapshot, poll_interval=interval)


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

    try:
        while not session.is_terminal:
            monitor_session(
                session,
                on_status_change=on_status_change,
            )
            time.sleep(poll_interval)

        console.print(f"\nSession ended with status: [bold]{session.status.value}[/bold]")
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped watching (session still running)[/dim]")


def _watch_agentd_session(
    client: AgentdClient,
    snapshot: AgentSessionSnapshot,
    *,
    poll_interval: float = 1.0,
) -> None:
    """Watch a session by polling agentd's persisted session snapshot."""
    current = snapshot
    last_status = current.status

    try:
        while not _is_terminal_snapshot(current):
            time.sleep(poll_interval)
            refreshed = client.get_session(current.session_id)
            if refreshed is None:
                console.print(
                    f"[red]Error:[/red] Session '{current.session_id}' disappeared from agentd"
                )
                sys.exit(1)
            current = refreshed
            if current.status != last_status:
                console.print(f"Status: [bold]{current.status.value}[/bold]")
                last_status = current.status

        console.print(f"\nSession ended with status: [bold]{current.status.value}[/bold]")
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped watching (session still running)[/dim]")
    except (AgentdClientError, OSError) as error:
        _print_agentd_request_error(error)
        sys.exit(1)


@cli.command()
@click.argument("session_name")
@click.argument("message")
def send(session_name: str, message: str) -> None:
    """Send a message to a running session."""
    client = _agentd_client_or_exit()
    snapshot = _agentd_session_or_exit(client, session_name)
    try:
        client.send_session(snapshot.session_id, message)
    except (AgentdClientError, OSError) as error:
        _print_agentd_request_error(error)
        sys.exit(1)
    console.print(f"[green]✓[/green] Sent message to {_agentd_display_name(snapshot)}")


@cli.command()
@click.argument("session_name")
@click.option("--lines", "-n", type=int, default=50, help="Number of lines to capture")
def output(session_name: str, lines: int) -> None:
    """Capture output from a session."""
    client = _agentd_client_or_exit()
    snapshot = _agentd_session_or_exit(client, session_name)
    try:
        output_text = client.capture_session(snapshot.session_id, lines=lines)
    except (AgentdClientError, OSError) as error:
        _print_agentd_request_error(error)
        sys.exit(1)
    console.print(output_text)


if __name__ == "__main__":
    cli()
