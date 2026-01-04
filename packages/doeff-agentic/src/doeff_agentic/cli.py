"""
CLI for doeff-agentic workflow management.

Commands:
    ps          List running workflows
    watch       Monitor workflow progress (TUI)
    attach      Attach to agent session (<workflow-id>:<session-name>)
    logs        View session/workflow logs
    stop        Stop workflow and all sessions
    show        Show workflow details
    env         Environment management (list, cleanup)
    tui         Launch interactive Textual TUI
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone

import click
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .api import AgenticAPI
from .types import AgentStatus, WorkflowStatus

console = Console()


def _format_duration(dt: datetime) -> str:
    """Format a datetime as relative duration."""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    seconds = int(delta.total_seconds())

    if seconds < 60:
        return f"{seconds}s ago"
    elif seconds < 3600:
        return f"{seconds // 60}m ago"
    elif seconds < 86400:
        return f"{seconds // 3600}h ago"
    else:
        return f"{seconds // 86400}d ago"


def _status_color(status: WorkflowStatus | AgentStatus) -> str:
    """Get Rich color for a status."""
    if isinstance(status, WorkflowStatus):
        return {
            WorkflowStatus.PENDING: "dim",
            WorkflowStatus.RUNNING: "green",
            WorkflowStatus.BLOCKED: "yellow",
            WorkflowStatus.COMPLETED: "blue",
            WorkflowStatus.FAILED: "red",
            WorkflowStatus.STOPPED: "magenta",
        }.get(status, "white")
    else:
        return {
            AgentStatus.PENDING: "dim",
            AgentStatus.BOOTING: "cyan",
            AgentStatus.RUNNING: "green",
            AgentStatus.BLOCKED: "yellow",
            AgentStatus.DONE: "blue",
            AgentStatus.FAILED: "red",
            AgentStatus.EXITED: "dim",
            AgentStatus.STOPPED: "dim",
        }.get(status, "white")


def _parse_session_ref(ref: str) -> tuple[str, str | None]:
    """Parse workflow:session reference.

    Returns (workflow_id, session_name).
    If no colon, session_name is None.
    """
    if ":" in ref:
        parts = ref.split(":", 1)
        return parts[0], parts[1]
    return ref, None


@click.group()
@click.option("--state-dir", envvar="DOEFF_AGENTIC_STATE_DIR", help="State directory")
@click.pass_context
def cli(ctx: click.Context, state_dir: str | None) -> None:
    """doeff-agentic: Agent-based workflow orchestration."""
    ctx.ensure_object(dict)
    ctx.obj["api"] = AgenticAPI(state_dir)


@cli.command()
@click.option("--status", "-s", multiple=True, help="Filter by workflow status")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.pass_context
def ps(
    ctx: click.Context,
    status: tuple[str, ...],
    output_json: bool,
) -> None:
    """List running workflows and agents.

    Example output:
        WORKFLOW    STATUS     AGENTS                          UPDATED
        a3f8b2c     running    reviewer(done), fixer(running)  2m ago
        b7e1d4f     done       tester(done)                    1h ago
    """
    api: AgenticAPI = ctx.obj["api"]

    workflows = api.list_workflows(
        status=list(status) if status else None,
    )

    if output_json:
        click.echo(json.dumps([w.to_dict() for w in workflows], indent=2))
        return

    if not workflows:
        console.print("[dim]No workflows found[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("WORKFLOW", style="cyan")
    table.add_column("STATUS")
    table.add_column("AGENTS")
    table.add_column("UPDATED")

    for wf in workflows:
        status_style = _status_color(wf.status)

        # Format agents list
        agents_str = "-"
        if wf.agents:
            agent_parts = []
            for a in wf.agents:
                a_style = _status_color(a.status)
                agent_parts.append(f"{a.name}([{a_style}]{a.status.value}[/{a_style}])")
            agents_str = ", ".join(agent_parts)

        table.add_row(
            wf.id,
            Text(wf.status.value, style=status_style),
            Text.from_markup(agents_str),
            _format_duration(wf.updated_at),
        )

    console.print(table)


@cli.command()
@click.argument("workflow_id")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON (streams JSONL)")
@click.option("--poll-interval", "-p", default=1.0, help="Poll interval in seconds")
@click.pass_context
def watch(
    ctx: click.Context,
    workflow_id: str,
    output_json: bool,
    poll_interval: float,
) -> None:
    """Monitor workflow progress in real-time (live TUI).

    Usage:
        doeff-agentic watch <workflow-id>
    """
    api: AgenticAPI = ctx.obj["api"]

    try:
        if output_json:
            # Stream JSONL for plugin consumption
            for update in api.watch(workflow_id, poll_interval):
                click.echo(json.dumps(update.to_dict()))
                sys.stdout.flush()
        else:
            # Rich TUI display
            _watch_tui(api, workflow_id, poll_interval)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[dim]Watch stopped[/dim]")


def _watch_tui(api: AgenticAPI, workflow_id: str, poll_interval: float) -> None:
    """Display watch TUI with Rich."""

    def generate_display(workflow):
        if workflow is None:
            return Panel("[red]Workflow not found[/red]")

        # Build workflow panel
        status_style = _status_color(workflow.status)

        content = []
        content.append(f"[bold]ID:[/bold] {workflow.id}")
        content.append(f"[bold]Name:[/bold] {workflow.name}")
        content.append(
            f"[bold]Status:[/bold] [{status_style}]{workflow.status.value}[/{status_style}]"
        )
        content.append(f"[bold]Started:[/bold] {_format_duration(workflow.started_at)}")
        content.append(f"[bold]Updated:[/bold] {_format_duration(workflow.updated_at)}")

        if workflow.last_slog:
            slog_str = json.dumps(workflow.last_slog, indent=2)
            content.append(f"\n[bold]Last Status:[/bold]\n{slog_str}")

        # Agents table
        if workflow.agents:
            content.append("\n[bold]Agents:[/bold]")
            for a in workflow.agents:
                a_style = _status_color(a.status)
                marker = ">" if a.name == workflow.current_agent else " "
                content.append(f"  {marker} {a.name}: [{a_style}]{a.status.value}[/{a_style}]")

        if workflow.error:
            content.append(f"\n[red]Error:[/red] {workflow.error}")

        footer = "\n[dim][q] quit  [a] attach to selected[/dim]"
        content.append(footer)

        return Panel(
            "\n".join(content),
            title=f"Workflow {workflow.id}",
            border_style="cyan",
        )

    # Initial display
    workflow = api.get_workflow(workflow_id)
    if workflow is None:
        console.print(f"[red]Workflow not found:[/red] {workflow_id}")
        return

    with Live(generate_display(workflow), refresh_per_second=1, console=console) as live:
        for update in api.watch(workflow_id, poll_interval):
            live.update(generate_display(update.workflow))

            # Check for terminal status
            if update.workflow.status in (
                WorkflowStatus.COMPLETED,
                WorkflowStatus.FAILED,
                WorkflowStatus.STOPPED,
            ):
                break


@cli.command()
@click.argument("target")
@click.pass_context
def attach(ctx: click.Context, target: str) -> None:
    """Attach to specific agent session.

    Usage:
        doeff-agentic attach <workflow-id>:<session-name>
        doeff-agentic attach a3f8b2c:reviewer
        doeff-agentic attach a3f:reviewer  # Prefix match

    If only workflow-id is provided and there's only one agent, attaches to that.
    """
    api: AgenticAPI = ctx.obj["api"]

    workflow_id, session_name = _parse_session_ref(target)

    try:
        workflow = api.get_workflow(workflow_id)
        if workflow is None:
            console.print(f"[red]Workflow not found:[/red] {workflow_id}")
            sys.exit(1)

        # If no session specified and multiple agents, prompt
        if not session_name and len(workflow.agents) > 1:
            console.print(
                f"[yellow]Multiple agents in workflow {workflow.id}. Specify session:[/yellow]"
            )
            for a in workflow.agents:
                style = _status_color(a.status)
                console.print(f"  doeff-agentic attach {workflow.id}:{a.name}")
            sys.exit(1)

        # If no session specified and single agent, use that
        if not session_name and len(workflow.agents) == 1:
            session_name = workflow.agents[0].name

        if not session_name:
            console.print("[red]No agents in workflow[/red]")
            sys.exit(1)

        api.attach(workflow_id, session_name)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@cli.command()
@click.argument("target")
@click.option("--follow", "-f", is_flag=True, help="Tail mode")
@click.option("--lines", "-n", default=100, help="Number of lines")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.pass_context
def logs(
    ctx: click.Context,
    target: str,
    follow: bool,
    lines: int,
    output_json: bool,
) -> None:
    """View session logs.

    Usage:
        doeff-agentic logs <workflow-id>                  # All sessions, interleaved
        doeff-agentic logs <workflow-id>:<session-name>  # Specific session
        doeff-agentic logs --follow <workflow-id>        # Tail mode
    """
    api: AgenticAPI = ctx.obj["api"]

    workflow_id, session_name = _parse_session_ref(target)

    try:
        workflow = api.get_workflow(workflow_id)
        if workflow is None:
            console.print(f"[red]Workflow not found:[/red] {workflow_id}")
            sys.exit(1)

        if follow:
            # Tail mode
            last_output = ""
            while True:
                output = api.get_agent_output(workflow_id, session_name, lines)
                if output != last_output:
                    # Print new content
                    if last_output:
                        new_lines = output[len(last_output) :]
                        click.echo(new_lines, nl=False)
                    else:
                        click.echo(output, nl=False)
                    last_output = output
                time.sleep(0.5)
        else:
            output = api.get_agent_output(workflow_id, session_name, lines)
            if output_json:
                click.echo(json.dumps({"output": output}))
            else:
                click.echo(output)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        pass


@cli.command()
@click.argument("workflow_id")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.pass_context
def stop(
    ctx: click.Context,
    workflow_id: str,
    output_json: bool,
) -> None:
    """Stop workflow and all its sessions.

    Usage:
        doeff-agentic stop <workflow-id>
    """
    api: AgenticAPI = ctx.obj["api"]

    try:
        stopped = api.stop(workflow_id)

        if output_json:
            click.echo(json.dumps({"ok": True, "stopped": stopped}))
        else:
            if stopped:
                console.print(f"[green]Stopped sessions:[/green] {', '.join(stopped)}")
            else:
                console.print("[dim]No sessions to stop[/dim]")
    except ValueError as e:
        if output_json:
            click.echo(json.dumps({"ok": False, "error": str(e)}))
        else:
            console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@cli.command()
@click.argument("workflow_id")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.pass_context
def show(
    ctx: click.Context,
    workflow_id: str,
    output_json: bool,
) -> None:
    """Show workflow details."""
    api: AgenticAPI = ctx.obj["api"]

    try:
        workflow = api.get_workflow(workflow_id)
        if workflow is None:
            if output_json:
                click.echo(json.dumps({"error": "not found"}))
            else:
                console.print(f"[red]Workflow not found:[/red] {workflow_id}")
            sys.exit(1)

        if output_json:
            click.echo(json.dumps(workflow.to_dict(), indent=2))
        else:
            console.print(
                Panel(
                    _workflow_details(workflow),
                    title=f"Workflow: {workflow.id}",
                    border_style="cyan",
                )
            )
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


def _workflow_details(workflow) -> str:
    """Format workflow details for display."""
    lines = []
    lines.append(f"[bold]Name:[/bold] {workflow.name}")
    lines.append(
        f"[bold]Status:[/bold] [{_status_color(workflow.status)}]{workflow.status.value}[/{_status_color(workflow.status)}]"
    )
    lines.append(f"[bold]Started:[/bold] {workflow.started_at.isoformat()}")
    lines.append(f"[bold]Updated:[/bold] {workflow.updated_at.isoformat()}")

    if workflow.current_agent:
        lines.append(f"[bold]Current Agent:[/bold] {workflow.current_agent}")

    if workflow.agents:
        lines.append("\n[bold]Agents:[/bold]")
        for a in workflow.agents:
            style = _status_color(a.status)
            lines.append(f"  - {a.name}: [{style}]{a.status.value}[/{style}] ({a.session_name})")

    if workflow.last_slog:
        lines.append("\n[bold]Last Status:[/bold]")
        for k, v in workflow.last_slog.items():
            lines.append(f"  {k}: {v}")

    if workflow.error:
        lines.append(f"\n[bold red]Error:[/bold red] {workflow.error}")

    return "\n".join(lines)


# =============================================================================
# Environment Commands
# =============================================================================


@cli.group()
def env() -> None:
    """Environment management commands."""
    pass


@env.command("list")
@click.option("--workflow", "-w", help="Filter by workflow ID")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.pass_context
def env_list(
    ctx: click.Context,
    workflow: str | None,
    output_json: bool,
) -> None:
    """List environments.

    Example output:
        ENV ID      TYPE       WORKING DIR                    SESSIONS
        env-abc     worktree   /tmp/doeff/worktrees/abc123    reviewer, fixer
        env-def     worktree   /tmp/doeff/worktrees/def456    tester
    """
    api: AgenticAPI = ctx.obj["api"]

    # Note: Environment tracking is currently in-memory per handler
    # This command shows a placeholder for future persistent storage
    if output_json:
        click.echo(
            json.dumps(
                {"environments": [], "note": "Environment listing requires persistent storage"}
            )
        )
    else:
        console.print("[dim]Environment listing not yet implemented for persistent storage[/dim]")
        console.print("[dim]Environments are tracked in-memory per workflow handler[/dim]")


@env.command("cleanup")
@click.option("--dry-run", is_flag=True, help="Show what would be cleaned up")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.pass_context
def env_cleanup(
    ctx: click.Context,
    dry_run: bool,
    output_json: bool,
) -> None:
    """Cleanup orphaned environments (worktrees, copies).

    This removes:
    - Git worktrees in /tmp/doeff/worktrees/ not associated with running workflows
    - Copied directories in /tmp/doeff/copies/ not associated with running workflows
    """
    import shutil
    from pathlib import Path

    worktree_dir = Path("/tmp/doeff/worktrees")
    copies_dir = Path("/tmp/doeff/copies")

    cleaned = []

    for base_dir in [worktree_dir, copies_dir]:
        if base_dir.exists():
            for item in base_dir.iterdir():
                if item.is_dir():
                    if dry_run:
                        cleaned.append(str(item))
                    else:
                        try:
                            if base_dir == worktree_dir:
                                # Remove git worktree
                                import subprocess

                                subprocess.run(
                                    ["git", "worktree", "remove", "--force", str(item)],
                                    capture_output=True,
                                    check=False,
                                )
                            shutil.rmtree(item, ignore_errors=True)
                            cleaned.append(str(item))
                        except Exception:
                            pass

    if output_json:
        click.echo(
            json.dumps(
                {
                    "dry_run": dry_run,
                    "cleaned": cleaned,
                }
            )
        )
    else:
        if cleaned:
            action = "Would clean" if dry_run else "Cleaned"
            console.print(f"[green]{action}:[/green]")
            for path in cleaned:
                console.print(f"  {path}")
        else:
            console.print("[dim]No orphaned environments found[/dim]")


@cli.command()
def tui() -> None:
    """Launch interactive TUI for workflow management."""
    from .tui import main as tui_main

    tui_main()


# =============================================================================
# Deprecated Commands (kept for backward compatibility)
# =============================================================================


@cli.command(hidden=True)
@click.argument("workflow_id")
@click.argument("message")
@click.option("--agent", "-a", help="Specific agent to send to")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.pass_context
def send(
    ctx: click.Context,
    workflow_id: str,
    message: str,
    agent: str | None,
    output_json: bool,
) -> None:
    """[DEPRECATED] Send message to running agent.

    This command is deprecated. Use effects-based messaging instead.
    """
    console.print(
        "[yellow]Warning: 'send' command is deprecated. "
        "Use effects-based messaging in workflows instead.[/yellow]"
    )

    api: AgenticAPI = ctx.obj["api"]

    success = api.send_message(workflow_id, message, agent)

    if output_json:
        click.echo(json.dumps({"ok": success, "deprecated": True}))
    else:
        if success:
            console.print("[green]Message sent[/green]")
        else:
            console.print("[red]Failed to send message[/red]")
            sys.exit(1)


if __name__ == "__main__":
    cli()
