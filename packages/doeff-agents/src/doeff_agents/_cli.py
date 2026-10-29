"""CLI for doeff-agents: each command runs a program of doeff-agents effects under handlers.

A command builds a program that yields effects — the public session effects
(``doeff_agents._effects``) and, for the ``agentd`` verbs that speak the host
protocol itself and for ``ps``'s unreadable rows, the internal host asks
(``doeff_agents._effects._session_host_effects``) — and runs it under the
handlers of ``doeff_agents._handlers._session_host``.  The command never builds a
client: the one thing it hands those handlers is whether reaching no host may
start one (``start_host``).
"""

import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import click
from doeff_time import Delay
from rich.console import Console
from rich.table import Table

from doeff import Program, do, run, with_handlers
from doeff_agents._agentd_client import (
    AgentdClientError,
    AgentdSessionList,
    AgentdSessionParseWarning,
    AgentdUnavailableError,
)
from doeff_agents._effects import (
    AgentError,
    AgentSessionSnapshot,
    AgentType,
    AttachAgentSession,
    CancelAgentSession,
    Capture,
    GetAgentSession,
    Launch,
    ListAgentSessions,
    Monitor,
    Send,
    SessionHandle,
    SessionNotFoundError,
    SessionStatus,
)
from doeff_agents._effects._session_host_effects import (
    HostKindsReading,
    HostStatusReading,
    ListSessionRowsEffect,
    SessionHostCallEffect,
    SessionHostKindsEffect,
    SessionHostStatusEffect,
)
from doeff_agents._handlers._session_host import (
    session_host_handlers,
    terminal_session_handlers,
)
from doeff_agents._tmux import TmuxError

_T = TypeVar("_T")

console = Console()

# All doeff-agents sessions are prefixed with this to distinguish from other tmux sessions
SESSION_PREFIX = "doeff-"
AGENTD_UNAVAILABLE_HINT = "agentd が起動していません。doeff-agents agentd ensure(または python -m doeff_agents._sessionhost.host ...)を実行してください。"
TERMINAL_STATUSES = {
    SessionStatus.DONE,
    SessionStatus.FAILED,
    SessionStatus.EXITED,
    SessionStatus.STOPPED,
}
#: What a command's program may fail with: the host unreachable or refusing
#: (client errors and socket errors), a session effect refused, tmux failing.
_COMMAND_ERRORS = (AgentdClientError, AgentError, TmuxError, OSError)

#: The handler factories the commands run under — module names so a test can
#: put a fake host or a fake clock in their place.
_session_host_handlers = session_host_handlers
_terminal_session_handlers = terminal_session_handlers


@dataclass(frozen=True)
class _Failed:
    """A command's program ended in one of ``_COMMAND_ERRORS`` (kept as a value so ``run`` prints no traceback)."""

    error: Exception


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


@do
def _caught(program: Program[_T]):
    """Run PROGRAM and turn an expected failure into a value, so the command can report it in one line."""
    try:
        return (yield program)
    except _COMMAND_ERRORS as error:
        return _Failed(error)


def _run_on_host(program: Program[_T], *, start_host: bool) -> _T:
    """Run a command's PROGRAM under the session-host handlers; a failure ends the command with exit 1.

    ``start_host`` is the only host-start decision of the command
    (ADR-DOE-AGENTS-004 law ``reads-never-start-a-host``): the observation
    verbs pass False unless the operator gave ``--ensure``.
    """
    outcome = run(with_handlers(_session_host_handlers(start_host=start_host), _caught(program)))
    if isinstance(outcome, _Failed):
        _print_error(outcome.error)
        sys.exit(1)
    return outcome


#: The `--ensure` flag shared by the observation verbs (one declaration, so the
#: opt-in reads the same on every one of them).
_ensure_option = click.option(
    "--ensure",
    is_flag=True,
    help="Start a host if none is reachable. Off by default: reading never starts one.",
)


def _print_error(error: Exception) -> None:
    """Tell the operator why the command stopped; an unreachable host comes with the command that starts one."""
    if isinstance(error, (AgentdUnavailableError, OSError)):
        console.print(f"[red]Error:[/red] {AGENTD_UNAVAILABLE_HINT}")
        console.print(f"[dim]{error}[/dim]")
        return
    console.print(f"[red]Error:[/red] {error}")


def _print_agentd_parse_warnings(warnings: tuple[AgentdSessionParseWarning, ...]) -> None:
    """Name each host row ``ps`` skipped, so a host newer than this client does not hide sessions silently."""
    for warning in warnings:
        click.echo(
            "Warning: Skipping unparseable agentd session "
            f"{warning.session_name}: {warning.field}={warning.raw_value!r}",
            err=True,
        )


@do
def _find_session(user_name: str):
    """The host's session an operator named (its id, its tmux name, or that name without the prefix).

    Raises ``SessionNotFoundError`` when none matches and ``AgentError`` when
    the name matches more than one, so every verb that takes a name reports
    the two cases the same way.
    """
    candidate_ids = _agentd_session_id_candidates(user_name)
    for session_id in candidate_ids:
        snapshot = yield GetAgentSession(session_id)
        if snapshot is not None:
            return snapshot

    snapshots = yield ListAgentSessions()
    matches = [
        snapshot
        for snapshot in snapshots
        if _agentd_snapshot_matches_name(snapshot, user_name, candidate_ids)
    ]
    if len(matches) > 1:
        names = ", ".join(snapshot.session_id for snapshot in matches)
        raise AgentError(f"Session name '{user_name}' is ambiguous in agentd: {names}")
    if len(matches) == 1:
        return matches[0]
    raise SessionNotFoundError(f"Session '{user_name}' not found in agentd")


def _agentd_session_id_candidates(user_name: str) -> tuple[str, ...]:
    """The session ids an operator's name may stand for (as typed, and with the tmux prefix)."""
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
    """Whether SNAPSHOT is the session an operator's name stands for."""
    return (
        snapshot.session_id in candidate_ids
        or snapshot.session_name in candidate_ids
        or _to_display_name(snapshot.session_name) == user_name
    )


def _agentd_display_name(snapshot: AgentSessionSnapshot) -> str:
    """The name an operator sees and types for SNAPSHOT."""
    return _to_display_name(snapshot.session_name)


@click.group()
def cli() -> None:
    """doeff-agents: Agent session management for coding agents."""


@cli.group()
def agentd() -> None:
    """Manage the session host (the retired `doeff-agentd` binary is now
    the entry module `doeff_agents._sessionhost.host`; only the socket, database and protocol keep the
    `agentd` name)."""


@agentd.command("kinds")
@click.option("--json", "json_output", is_flag=True, help="Print machine-readable kinds.")
def agentd_kinds(json_output: bool) -> None:
    """Print the running host's advertised binding-kind vocabulary.

    Deliberately does NOT ensure/spawn the daemon: kind verification is a
    read-only observation and must never couple to host liveness (an
    unreachable host means "no observation", not "start a host").
    """
    reading: HostKindsReading = _run_on_host(_ask(SessionHostKindsEffect()), start_host=False)
    payload = {"socket_path": str(reading.socket_path), "kinds": [dict(r) for r in reading.kinds]}
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
    """Ensure the session host is reachable, starting it if necessary."""
    reading: HostStatusReading = _run_on_host(_ask(SessionHostStatusEffect()), start_host=True)
    status = dict(reading.status)
    payload = {
        "socket_path": str(reading.socket_path),
        "status": status,
        "supervised": reading.supervised,
    }
    daemon_db = status.get("db_path")
    if isinstance(daemon_db, str):
        payload["db_path"] = daemon_db

    if json_output:
        click.echo(json.dumps(payload, sort_keys=True))
        return

    console.print(f"session host: {status.get('state', 'running')}")
    console.print(f"socket: {reading.socket_path}")
    if isinstance(daemon_db, str):
        console.print(f"db: {daemon_db}")
    if reading.supervised:
        console.print("supervised: true (start/restart is owned by the declared supervisor)")


@do
def _ask(effect):
    """The program that yields EFFECT once and returns its answer (a verb with nothing else to do)."""
    return (yield effect)


def _call_host(method: str, params: dict[str, object], *, start_host: bool) -> None:
    """Pass one host-protocol request through and print the host's JSON answer (the `agentd` ops verbs)."""
    result = _run_on_host(_ask(SessionHostCallEffect(method=method, params=params)), start_host=start_host)
    click.echo(json.dumps(result, ensure_ascii=False, sort_keys=True))


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
@click.option(
    "--conversation-id",
    default=None,
    help="Conversation identity (波 1-S1: written to conversation_json).",
)
def agentd_adopt(
    session_name: str,
    substrate_kind: str,
    substrate_ref: str,
    agent_kind: str,
    lifecycle: str,
    display_name: str | None,
    work_dir: str | None,
    conversation_id: str | None,
) -> None:
    """Register an ALREADY-LIVE seat in the session ledger (koine
    session.adopt — observation-only; existence is verified before
    registration, a failed adopt leaves no row)."""
    params: dict[str, object] = {
        "session_name": session_name,
        "substrate": {"kind": substrate_kind, "ref": substrate_ref},
        "agent_kind": agent_kind,
        "lifecycle": lifecycle,
    }
    if display_name is not None:
        params["name"] = display_name
    if work_dir is not None:
        params["work_dir"] = work_dir
    if conversation_id is not None:
        params["conversation_id"] = conversation_id
    _call_host("session.adopt", params, start_host=True)


def _turn_descriptor(
    pane_id: str | None, agent_name: str | None, conversation_id: str | None
) -> dict[str, object]:
    """The seat descriptor the turn stamps resolve; at least one key must be given."""
    if pane_id is None and agent_name is None and conversation_id is None:
        console.print(
            "[red]Error:[/red] provide --pane-id, --conversation-id and/or --agent-name"
        )
        sys.exit(1)
    descriptor: dict[str, object] = {}
    if pane_id is not None:
        descriptor["pane_id"] = pane_id
    if agent_name is not None:
        descriptor["agent_name"] = agent_name
    if conversation_id is not None:
        descriptor["conversation_id"] = conversation_id
    return descriptor


@agentd.command("turn-open")
@click.option("--pane-id", default=None, help="Descriptor first key (e.g. HERDR_PANE_ID).")
@click.option("--agent-name", default=None, help="Descriptor name key (bare descriptors only).")
@click.option(
    "--conversation-id",
    default=None,
    help="Descriptor identity key (波 1-S1: conversation_json.session_id).",
)
def agentd_turn_open(
    pane_id: str | None, agent_name: str | None, conversation_id: str | None
) -> None:
    """Stamp turn-open (holder=agent) on the adopted seat resolved from the
    descriptor. Ops/debug binding — the seat's hook hot path writes the raw
    socket line instead (<=200ms fire-and-forget; a CLI start-up would
    break that budget)."""
    _call_host(
        "session.turn_open",
        {"descriptor": _turn_descriptor(pane_id, agent_name, conversation_id)},
        start_host=True,
    )


@agentd.command("by-conversation")
@click.option("--conversation-id", required=True, help="Conversation identity to resolve.")
@_ensure_option
def agentd_by_conversation(conversation_id: str, ensure: bool) -> None:
    """Resolve conversation_id to its ledger row (波 1-S1 session.by_conversation
    — probe-free read: newest non-terminal row, else newest terminal row,
    else null; substrate_present is intentionally absent).  Read-only: does not
    start a host."""
    _call_host(
        "session.by_conversation", {"conversation_id": conversation_id}, start_host=ensure
    )


@agentd.command("turn-close")
@click.option("--pane-id", default=None, help="Descriptor first key (e.g. HERDR_PANE_ID).")
@click.option("--agent-name", default=None, help="Descriptor name key (bare descriptors only).")
@click.option(
    "--conversation-id",
    default=None,
    help="Descriptor identity key (波 1-S1: conversation_json.session_id).",
)
@click.option("--wait-who", default=None, help="WAIT destination (user/work/...).")
@click.option("--wait-kind", default=None, help="WAIT kind passthrough (decide/review/...).")
@click.option("--wait-reason", default=None, help="WAIT reason passthrough.")
def agentd_turn_close(
    pane_id: str | None,
    agent_name: str | None,
    conversation_id: str | None,
    wait_who: str | None,
    wait_kind: str | None,
    wait_reason: str | None,
) -> None:
    """Stamp turn-close on the adopted seat resolved from the descriptor.
    holder becomes wait.who (or "work" without a wait); the wait fields are
    stored opaquely — the parse authority stays with the seat's wait
    protocol."""
    params: dict[str, object] = {
        "descriptor": _turn_descriptor(pane_id, agent_name, conversation_id)
    }
    wait: dict[str, object] = {}
    if wait_who is not None:
        wait["who"] = wait_who
    if wait_kind is not None:
        wait["kind"] = wait_kind
    if wait_reason is not None:
        wait["reason"] = wait_reason
    if wait:
        params["wait"] = wait
    _call_host("session.turn_close", params, start_host=True)


@cli.command("run")
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
def run_command(
    agent: str,
    work_dir: Path,
    prompt: str | None,
    model: str | None,
    session: str | None,
    watch: bool,
) -> None:
    """Launch a new agent session."""
    display_name = session or f"{agent}-{uuid.uuid4().hex[:8]}"
    launch = Launch(
        # Always use prefixed name for tmux
        _to_tmux_name(display_name),
        agent_type=AgentType(agent),
        work_dir=work_dir.absolute(),
        prompt=prompt,
        model=model,
    )

    @do
    def launch_and_watch():
        """Launch the session, say so, and follow its status until it ends when asked to watch."""
        handle = yield launch
        console.print(f"[green]✓[/green] Launched session: [bold]{display_name}[/bold]")
        console.print(f"  Agent: {agent}")
        console.print(f"  Work dir: {work_dir.absolute()}")
        if watch:
            console.print("\nWatching session (Ctrl+C to stop)...")
            yield _watch_launched(handle)

    try:
        outcome = run(with_handlers(_terminal_session_handlers(), _caught(launch_and_watch())))
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped watching (session still running)[/dim]")
        return
    if isinstance(outcome, _Failed):
        console.print(f"[red]Error:[/red] {outcome.error}")
        sys.exit(1)



@do
def _watch_launched(handle: SessionHandle, poll_interval: float = 1.0):
    """Print each status a launched session moves to, until it ends."""
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
    last_status: SessionStatus | None = None
    while True:
        observation = yield Monitor(handle)
        if observation.status != last_status:
            color = status_colors.get(observation.status, "white")
            console.print(f"Status: [{color}]{observation.status.value}[/{color}]")
            last_status = observation.status
        if observation.status in TERMINAL_STATUSES:
            break
        yield Delay(poll_interval)
    console.print(f"\nSession ended with status: [bold]{observation.status.value}[/bold]")


@cli.command("ps")
@click.option(
    "--all",
    "-a",
    "show_all",
    is_flag=True,
    help="Show all agentd sessions (kept for CLI compatibility)",
)
@_ensure_option
def ps_command(show_all: bool, ensure: bool) -> None:
    """List doeff-agents sessions (read-only: does not start a host)."""
    session_list: AgentdSessionList = _run_on_host(_ask(ListSessionRowsEffect()), start_host=ensure)

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

    @do
    def find_and_attach():
        """Find the named session, say which it is, and join its terminal until the operator detaches."""
        snapshot = yield _find_session(session_name)
        console.print(f"Attaching to session: {_agentd_display_name(snapshot)}")
        console.print("Press Ctrl+B, D to detach")
        yield AttachAgentSession(snapshot.session_id)

    _run_on_host(find_and_attach(), start_host=True)


@cli.command()
@click.argument("session_name")
def stop(session_name: str) -> None:
    """End a session through the host, whatever backend carries it.

    The host is the single authority over a session's life (ADR-DOE-AGENTS-004
    law `daemon-owns-only-exteriority`), and `session.cancel` is backend-blind:
    the substrate installed for tmux, herdr or headless answers the same kill
    effect.  So this command does not branch on the backend — a branch here
    would be a second place that has to learn every new substrate, and the
    tmux-only version of it reported "Session not found" for every herdr and
    headless session.
    """

    @do
    def find_and_cancel():
        """Find the named session and have the host cancel it."""
        snapshot = yield _find_session(session_name)
        return (yield CancelAgentSession(snapshot.session_id))

    stopped: AgentSessionSnapshot = _run_on_host(find_and_cancel(), start_host=True)
    console.print(
        f"[green]✓[/green] Stopped session: {_agentd_display_name(stopped)} "
        f"(backend: {stopped.backend_kind}, status: {stopped.status.value})"
    )


@cli.command("watch")
@click.argument("session_name")
@click.option("--interval", "-i", type=float, default=1.0, help="Poll interval in seconds")
@_ensure_option
def watch_command(session_name: str, interval: float, ensure: bool) -> None:
    """Monitor a running session (read-only: does not start a host)."""
    try:
        _run_on_host(_watch_on_host(session_name, interval), start_host=ensure)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped watching (session still running)[/dim]")


@do
def _watch_on_host(session_name: str, poll_interval: float):
    """Print each status the named session moves to, reading the host's snapshot, until it ends."""
    current = yield _find_session(session_name)
    console.print(f"Watching session: {_agentd_display_name(current)} (Ctrl+C to stop)")
    last_status = current.status
    while current.status not in TERMINAL_STATUSES:
        yield Delay(poll_interval)
        refreshed = yield GetAgentSession(current.session_id)
        if refreshed is None:
            raise SessionNotFoundError(
                f"Session '{current.session_id}' disappeared from agentd"
            )
        current = refreshed
        if current.status != last_status:
            console.print(f"Status: [bold]{current.status.value}[/bold]")
            last_status = current.status
    console.print(f"\nSession ended with status: [bold]{current.status.value}[/bold]")


@cli.command()
@click.argument("session_name")
@click.argument("message")
def send(session_name: str, message: str) -> None:
    """Send a message to a running session."""

    @do
    def find_and_send():
        """Find the named session and type MESSAGE into it through the host."""
        snapshot = yield _find_session(session_name)
        yield Send(SessionHandle(snapshot.session_id), message)
        return snapshot

    snapshot: AgentSessionSnapshot = _run_on_host(find_and_send(), start_host=True)
    console.print(f"[green]✓[/green] Sent message to {_agentd_display_name(snapshot)}")


@cli.command()
@click.argument("session_name")
@click.option("--lines", "-n", type=int, default=50, help="Number of lines to capture")
@_ensure_option
def output(session_name: str, lines: int, ensure: bool) -> None:
    """Capture output from a session (read-only: does not start a host)."""

    @do
    def find_and_capture():
        """Find the named session and read its last LINES lines of output from the host."""
        snapshot = yield _find_session(session_name)
        return (yield Capture(SessionHandle(snapshot.session_id), lines=lines))

    output_text: str = _run_on_host(find_and_capture(), start_host=ensure)
    console.print(output_text)


if __name__ == "__main__":
    cli()
