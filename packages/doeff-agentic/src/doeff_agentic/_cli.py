"""
CLI for doeff-agentic workflow management (the console script ``doeff-agentic``).

Commands:
    ps          List workflows and agents
    show        Show workflow details
    watch       Stream workflow updates
    attach      Attach to an agent's tmux session
    send        Send a message to an agent (or answer WaitForUserInput)
    stop        Stop workflow and kill its agents
    logs        View an agent's output
    env         Environment management (list, cleanup)
    tui         Launch interactive Textual TUI

ps, show, watch, attach, send and stop take the arguments of the Rust CLI
(packages/doeff-agentic-cli) and write the same JSON, as the IDE plugins read it: the
JSON of a workflow is ``WorkflowInfo.to_dict()``, ``ps --json`` writes the array of them.
A failure writes ``Error: <message>`` to the standard error and exits with 1.

Each command runs a doeff program: the program reads and acts on the recorded workflows
with the effects of ``doeff_agentic._effects.recorded``, reads the clock and waits
between polls with doeff-time's GetTime and Delay, and writes its output with Echo. The
command is the composition root that installs the handlers — ``recorded_workflows_handler``
over the state directory, ``sync_time_handler`` for the clock and ``_terminal_handler``
for the output (a test puts its own handlers in ``obj["handlers"]``).
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click
from doeff_core_effects import try_handler
from doeff_time import Delay, GetTime, sync_time_handler

from doeff import (
    Effect,
    EffectBase,
    Err,
    Pass,
    Program,
    Resume,
    Try,
    do,
    handler,
    run,
    with_handlers,
)
from doeff.program import ProgramHandler
from doeff_agentic._effects.recorded import (
    AgenticAnswerInput,
    AgenticAttachAgent,
    AgenticCaptureAgentOutput,
    AgenticCleanupEnvironments,
    AgenticFindWorkflow,
    AgenticInputRequested,
    AgenticListEnvironments,
    AgenticListWorkflows,
    AgenticSendToAgent,
    AgenticStopWorkflow,
)
from doeff_agentic._handlers.recorded import recorded_workflows_handler
from doeff_agentic._types import WorkflowInfo, WorkflowStatus

_FINISHED = (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.STOPPED)

#: How often ``logs --follow`` reads the agent's output again (seconds).
_LOGS_POLL = 0.5


class CommandError(Exception):
    """A command cannot do what it was asked (written as ``Error: <message>``, exit 1)."""


@dataclass(frozen=True, kw_only=True)
class Echo(EffectBase[None]):
    """Write ``text`` to the standard output, ending the line unless ``newline`` is False."""

    text: str
    newline: bool = True


@handler
def _terminal_handler(effect: Effect, k: Any):
    """Answer Echo by writing to the standard output."""
    if isinstance(effect, Echo):
        click.echo(effect.text, nl=effect.newline)
        return (yield Resume(k, None))
    return (yield Pass(effect, k))


@do
def _attempt(program: Program[None, Any]):
    """Catch the command program's failure as a value, so ``run`` prints no doeff traceback."""
    return (yield Try(program))


def _run(ctx: click.Context, program: Program[None, Any]) -> None:
    """Run a command's program under the handlers ``cli`` installed.

    A CommandError, or the ValueError AgenticFindWorkflow raises for a prefix that names
    several workflows, is written as ``Error: <message>`` and exits with 1.
    """
    handlers: tuple[ProgramHandler, ...] = ctx.obj["handlers"]
    outcome = run(with_handlers([try_handler, _terminal_handler, *handlers], _attempt(program)))
    if isinstance(outcome, Err):
        if not isinstance(outcome.error, (CommandError, ValueError)):
            raise outcome.error
        click.echo(f"Error: {outcome.error}", err=True)
        ctx.exit(1)


def _pretty(value: object) -> str:
    """JSON with an indent of 2, as serde_json's to_string_pretty writes it."""
    return json.dumps(value, indent=2, ensure_ascii=False)


def _compact(value: object) -> str:
    """JSON on one line without spaces, as serde_json's to_string writes it."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _ago(at: datetime, now: datetime) -> str:
    """How long ago ``at`` was, as ``12s ago`` / ``3m ago`` / ``5h ago`` / ``2d ago``."""
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    seconds = int((now - at).total_seconds())
    if seconds < 0:
        return "in the future"
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """An ASCII table with a rule between the rows."""
    widths = [max(len(cell) for cell in column) for column in zip(headers, *rows, strict=True)]
    rule = "+" + "+".join("-" * (width + 2) for width in widths) + "+"

    def line(cells: Sequence[str]) -> str:
        return (
            "|"
            + "|".join(f" {cell.ljust(width)} " for cell, width in zip(cells, widths, strict=True))
            + "|"
        )

    body = tuple(part for row in rows for part in (line(row), rule))
    return "\n".join((rule, line(headers), rule, *body))


def _session_of(workflow: WorkflowInfo, agent: str) -> str:
    """The tmux session of an agent (``doeff-{id}-{agent}`` when the agent is not recorded)."""
    recorded = next((a for a in workflow.agents if a.name == agent), None)
    return recorded.session_name if recorded else f"doeff-{workflow.id}-{agent}"


def _some_agent(workflow: WorkflowInfo, agent: str | None) -> str | None:
    """``agent``, else the workflow's current agent, else its first agent."""
    if agent is not None:
        return agent
    if workflow.current_agent is not None:
        return workflow.current_agent
    return workflow.agents[0].name if workflow.agents else None


@do
def _find(workflow_id: str):
    """The recorded workflow ``workflow_id`` names; CommandError when there is none."""
    workflow = yield AgenticFindWorkflow(workflow_id=workflow_id)
    if workflow is None:
        raise CommandError(f"Workflow not found: {workflow_id}")
    return workflow


@click.group()
@click.option(
    "--state-dir",
    envvar="DOEFF_AGENTIC_STATE_DIR",
    type=click.Path(path_type=Path),
    help="State directory (defaults to ~/.local/state/doeff-agentic)",
)
@click.pass_context
def cli(ctx: click.Context, state_dir: Path | None) -> None:
    """doeff-agentic: Agent-based workflow orchestration."""
    ctx.ensure_object(dict)
    if "handlers" not in ctx.obj:
        ctx.obj["handlers"] = (sync_time_handler(), recorded_workflows_handler(state_dir))


# =============================================================================
# ps
# =============================================================================


@do
def _ps(statuses: tuple[str, ...], agent_statuses: tuple[str, ...], as_json: bool):
    recorded = yield AgenticListWorkflows()
    workflows = tuple(
        workflow
        for workflow in recorded
        if (not statuses or workflow.status.value in statuses)
        and (
            not agent_statuses
            or any(agent.status.value in agent_statuses for agent in workflow.agents)
        )
    )
    if as_json:
        yield Echo(text=_pretty([workflow.to_dict() for workflow in workflows]))
        return None
    if not workflows:
        yield Echo(text="No workflows found")
        return None

    now = yield GetTime()

    def row(workflow: WorkflowInfo) -> tuple[str, ...]:
        current = workflow.current_agent
        agent_status = next(
            (agent.status.value for agent in workflow.agents if agent.name == current), "-"
        )
        return (
            workflow.id,
            workflow.name,
            workflow.status.value,
            current or "-",
            agent_status if current else "-",
            _ago(workflow.updated_at, now),
        )

    headers = ("ID", "Workflow", "Status", "Agent", "Agent Status", "Updated")
    yield Echo(text=_table(headers, [row(workflow) for workflow in workflows]))


@cli.command()
@click.option("--status", "-s", multiple=True, help="Filter by workflow status")
@click.option("--agent-status", "-a", multiple=True, help="Filter by agent status")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def ps(
    ctx: click.Context, status: tuple[str, ...], agent_status: tuple[str, ...], as_json: bool
) -> None:
    """List running workflows and agents."""
    _run(ctx, _ps(status, agent_status, as_json))


# =============================================================================
# show
# =============================================================================


def _details(workflow: WorkflowInfo) -> str:
    current = (f"Current Agent: {workflow.current_agent}",) if workflow.current_agent else ()
    agents = (
        (
            "\nAgents:",
            *(
                f"  {'*' if agent.name == workflow.current_agent else ' '} "
                f"{agent.name}: {agent.status.value} ({agent.session_name})"
                for agent in workflow.agents
            ),
        )
        if workflow.agents
        else ()
    )
    slog = (f"\nLast Status: {_pretty(workflow.last_slog)}",) if workflow.last_slog else ()
    error = (f"\nError: {workflow.error}",) if workflow.error else ()
    return "\n".join(
        (
            f"ID: {workflow.id}",
            f"Name: {workflow.name}",
            f"Status: {workflow.status.value}",
            f"Started: {workflow.started_at.isoformat()}",
            f"Updated: {workflow.updated_at.isoformat()}",
            *current,
            *agents,
            *slog,
            *error,
        )
    )


@do
def _show(workflow_id: str, as_json: bool):
    workflow = yield _find(workflow_id)
    yield Echo(text=_pretty(workflow.to_dict()) if as_json else _details(workflow))


@cli.command()
@click.argument("workflow_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def show(ctx: click.Context, workflow_id: str, as_json: bool) -> None:
    """Show workflow details."""
    _run(ctx, _show(workflow_id, as_json))


# =============================================================================
# watch
# =============================================================================


def _watch_screen(workflow: WorkflowInfo, now: datetime) -> str:
    current = (f"Current Agent: {workflow.current_agent}",) if workflow.current_agent else ()
    slog = (f"\nLast Status: {_pretty(workflow.last_slog)}",) if workflow.last_slog else ()
    # Clear the screen and start at its top
    return "\x1b[2J\x1b[1;1H" + "\n".join(
        (
            f"Watching: {workflow.id} ({workflow.name})",
            f"Status: {workflow.status.value}",
            f"Updated: {_ago(workflow.updated_at, now)}",
            *current,
            *slog,
            "\n[Ctrl+C to stop]",
        )
    )


@do
def _watch(workflow_id: str, as_json: bool, poll: float):
    """Write the workflow whenever its update time changes, until it finishes or is deleted."""
    full_id = (yield _find(workflow_id)).id
    shown: datetime | None = None
    while True:
        workflow = yield AgenticFindWorkflow(workflow_id=full_id)
        if workflow is None:
            deleted = {"event": "deleted", "workflow_id": full_id}
            yield Echo(text=_compact(deleted) if as_json else "Workflow deleted")
            return None
        if workflow.updated_at != shown:
            if as_json:
                yield Echo(text=_compact(workflow.to_dict()))
            else:
                now = yield GetTime()
                yield Echo(text=_watch_screen(workflow, now))
            shown = workflow.updated_at
        if workflow.status in _FINISHED:
            if as_json:
                yield Echo(text=_compact({"event": "terminal", "status": workflow.status.value}))
            return None
        yield Delay(poll)


@cli.command()
@click.argument("workflow_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON (JSONL stream)")
@click.option("--poll", "-p", default=1.0, help="Poll interval in seconds")
@click.pass_context
def watch(ctx: click.Context, workflow_id: str, as_json: bool, poll: float) -> None:
    """Watch workflow updates (streams JSONL with --json)."""
    _run(ctx, _watch(workflow_id, as_json, poll))


# =============================================================================
# attach
# =============================================================================


@do
def _attach(workflow_id: str, agent: str | None):
    workflow = yield _find(workflow_id)
    target = _some_agent(workflow, agent)
    if target is None:
        raise CommandError("No agent to attach to")
    yield Echo(text=f"Attaching to: {_session_of(workflow, target)}")
    yield AgenticAttachAgent(workflow_id=workflow.id, agent=target)


@cli.command()
@click.argument("workflow_id")
@click.option("--agent", "-a", help="Specific agent name")
@click.pass_context
def attach(ctx: click.Context, workflow_id: str, agent: str | None) -> None:
    """Attach to agent's tmux session.

    WORKFLOW_ID is the workflow's ID or prefix. The agent is --agent, else the workflow's
    current agent, else its first agent.
    """
    _run(ctx, _attach(workflow_id, agent))


# =============================================================================
# send
# =============================================================================


@do
def _send(workflow_id: str, message: str, agent: str | None, as_json: bool):
    workflow = yield _find(workflow_id)
    if (yield AgenticInputRequested(workflow_id=workflow.id)):
        yield AgenticAnswerInput(workflow_id=workflow.id, text=message)
        answered = {"ok": True, "type": "user_input_response"}
        yield Echo(text=_compact(answered) if as_json else "Response sent")
        return None

    target = agent if agent is not None else workflow.current_agent
    if target is None:
        raise CommandError("No agent to send to")
    sent = yield AgenticSendToAgent(workflow_id=workflow.id, agent=target, message=message)
    if as_json:
        yield Echo(text=_compact({"ok": sent}))
    elif sent:
        yield Echo(text="Message sent")
    else:
        raise CommandError("Failed to send message")


@cli.command()
@click.argument("workflow_id")
@click.argument("message")
@click.option("--agent", "-a", help="Specific agent name")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def send(
    ctx: click.Context, workflow_id: str, message: str, agent: str | None, as_json: bool
) -> None:
    """Send message to running agent.

    When the workflow waits for the user's input (WaitForUserInput), MESSAGE answers it.
    Otherwise it is typed into the session of --agent, else of the workflow's current agent.
    """
    _run(ctx, _send(workflow_id, message, agent, as_json))


# =============================================================================
# stop
# =============================================================================


@do
def _stop(workflow_id: str, as_json: bool):
    workflow = yield _find(workflow_id)
    stopped = yield AgenticStopWorkflow(workflow_id=workflow.id)
    if as_json:
        yield Echo(text=_compact({"ok": True, "stopped": list(stopped)}))
    elif stopped:
        yield Echo(text=f"Stopped agents: {', '.join(stopped)}")
    else:
        yield Echo(text="No agents to stop")


@cli.command()
@click.argument("workflow_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def stop(ctx: click.Context, workflow_id: str, as_json: bool) -> None:
    """Stop workflow and kill agents."""
    _run(ctx, _stop(workflow_id, as_json))


# =============================================================================
# logs
# =============================================================================


@do
def _logs(workflow_id: str, agent: str | None, follow: bool, lines: int, as_json: bool):
    workflow = yield _find(workflow_id)
    target = _some_agent(workflow, agent)
    if target is None:
        raise CommandError("No agent in workflow")
    capture = AgenticCaptureAgentOutput(workflow_id=workflow.id, agent=target, lines=lines)
    if not follow:
        output = yield capture
        yield Echo(text=json.dumps({"output": output}) if as_json else output)
        return None

    shown = ""
    while True:
        output = yield capture
        if output != shown:
            yield Echo(text=output[len(shown) :] if shown else output, newline=False)
            shown = output
        yield Delay(_LOGS_POLL)


@cli.command()
@click.argument("workflow_id")
@click.option("--agent", "-a", help="Specific agent name")
@click.option("--follow", "-f", is_flag=True, help="Tail mode")
@click.option("--lines", "-n", default=100, help="Number of lines")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def logs(
    ctx: click.Context,
    workflow_id: str,
    agent: str | None,
    follow: bool,
    lines: int,
    as_json: bool,
) -> None:
    """View an agent's output.

    The agent is --agent, else the workflow's current agent, else its first agent.
    """
    _run(ctx, _logs(workflow_id, agent, follow, lines, as_json))


# =============================================================================
# env
# =============================================================================


@cli.group()
def env() -> None:
    """Environment management commands."""


@do
def _env_list(workflow_id: str | None, as_json: bool):
    recorded = yield AgenticListEnvironments(workflow_id=workflow_id)
    environments = [
        {
            "workflow_id": wf_id,
            "env_id": environment.id,
            "env_type": environment.env_type.value,
            "name": environment.name,
            "working_dir": environment.working_dir,
            "sessions": list(sessions),
        }
        for wf_id, environment, sessions in recorded
    ]
    if as_json:
        yield Echo(text=json.dumps({"environments": environments}))
        return None
    if not environments:
        yield Echo(text="No environments found")
        return None

    def row(environment: dict[str, Any]) -> tuple[str, ...]:
        work_dir = environment["working_dir"]
        return (
            environment["env_id"][:8],
            environment["env_type"],
            "..." + work_dir[-37:] if len(work_dir) > 40 else work_dir,
            ", ".join(environment["sessions"]) or "-",
        )

    headers = ("ENV ID", "TYPE", "WORKING DIR", "SESSIONS")
    yield Echo(text=_table(headers, [row(environment) for environment in environments]))


@env.command("list")
@click.option("--workflow", "-w", help="Filter by workflow ID")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def env_list(ctx: click.Context, workflow: str | None, as_json: bool) -> None:
    """List environments."""
    _run(ctx, _env_list(workflow, as_json))


@do
def _env_cleanup(dry_run: bool, as_json: bool):
    cleaned = yield AgenticCleanupEnvironments(dry_run=dry_run)
    if as_json:
        yield Echo(text=json.dumps({"dry_run": dry_run, "cleaned": list(cleaned)}))
    elif cleaned:
        action = "Would clean" if dry_run else "Cleaned"
        yield Echo(text="\n".join((f"{action}:", *(f"  {path}" for path in cleaned))))
    else:
        yield Echo(text="No environment directories found")


@env.command("cleanup")
@click.option("--dry-run", is_flag=True, help="Show what would be cleaned up")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def env_cleanup(ctx: click.Context, dry_run: bool, as_json: bool) -> None:
    """Remove every environment directory (worktrees, copies).

    This removes every directory under /tmp/doeff/worktrees/ (as a git worktree too) and
    /tmp/doeff/copies/, whether or not a workflow still uses it.
    """
    _run(ctx, _env_cleanup(dry_run, as_json))


# =============================================================================
# tui
# =============================================================================


@cli.command()
def tui() -> None:
    """Launch interactive TUI for workflow management."""
    from ._tui import main as tui_main

    tui_main()


def main() -> None:
    """The console script ``doeff-agentic``."""
    cli(prog_name="doeff-agentic")


__all__ = ["cli"]


if __name__ == "__main__":
    main()
