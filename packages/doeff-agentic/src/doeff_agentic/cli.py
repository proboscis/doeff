"""
CLI for doeff-agentic workflow management.

Commands:
    ps          List running workflows and agents
    watch       Monitor workflow and agent status
    attach      Attach to agent's tmux session
    logs        View agent output history
    stop        Stop workflow and kill agents
    send        Send message to agent
    show        Show workflow details
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


@click.group()
@click.option("--state-dir", envvar="DOEFF_AGENTIC_STATE_DIR", help="State directory")
@click.pass_context
def cli(ctx: click.Context, state_dir: str | None) -> None:
    """doeff-agentic: Agent-based workflow orchestration."""
    ctx.ensure_object(dict)
    ctx.obj["api"] = AgenticAPI(state_dir)


@cli.command()
@click.option("--status", "-s", multiple=True, help="Filter by workflow status")
@click.option("--agent-status", "-a", multiple=True, help="Filter by agent status")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.pass_context
def ps(
    ctx: click.Context,
    status: tuple[str, ...],
    agent_status: tuple[str, ...],
    output_json: bool,
) -> None:
    """List running workflows and agents."""
    api: AgenticAPI = ctx.obj["api"]

    workflows = api.list_workflows(
        status=list(status) if status else None,
        agent_status=list(agent_status) if agent_status else None,
    )

    if output_json:
        click.echo(json.dumps([w.to_dict() for w in workflows], indent=2))
        return

    if not workflows:
        console.print("[dim]No workflows found[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="cyan")
    table.add_column("Workflow")
    table.add_column("Status")
    table.add_column("Agent")
    table.add_column("Agent Status")
    table.add_column("Updated")

    for wf in workflows:
        status_style = _status_color(wf.status)
        agent_name = wf.current_agent or "-"

        # Get current agent status
        agent_st = "-"
        agent_st_style = "dim"
        if wf.current_agent:
            for a in wf.agents:
                if a.name == wf.current_agent:
                    agent_st = a.status.value
                    agent_st_style = _status_color(a.status)
                    break

        table.add_row(
            wf.id,
            wf.name,
            Text(wf.status.value, style=status_style),
            agent_name,
            Text(agent_st, style=agent_st_style),
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
    """Monitor workflow and agent status in real-time."""
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
        content.append(f"[bold]Status:[/bold] [{status_style}]{workflow.status.value}[/{status_style}]")
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
                marker = "*" if a.name == workflow.current_agent else " "
                content.append(f"  {marker} {a.name}: [{a_style}]{a.status.value}[/{a_style}]")

        if workflow.error:
            content.append(f"\n[red]Error:[/red] {workflow.error}")

        footer = "\n[dim][q] quit  [a] attach  [s] send message[/dim]"
        content.append(footer)

        return Panel(
            "\n".join(content),
            title=f"doeff-agentic watch {workflow.id}",
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
@click.argument("workflow_id")
@click.option("--agent", "-a", help="Specific agent to attach to")
@click.option("--last", is_flag=True, help="Attach to most recently active agent")
@click.pass_context
def attach(
    ctx: click.Context,
    workflow_id: str,
    agent: str | None,
    last: bool,
) -> None:
    """Attach to agent's tmux session."""
    api: AgenticAPI = ctx.obj["api"]

    try:
        workflow = api.get_workflow(workflow_id)
        if workflow is None:
            console.print(f"[red]Workflow not found:[/red] {workflow_id}")
            sys.exit(1)

        # If multiple agents and none specified, prompt
        if not agent and len(workflow.agents) > 1 and not last:
            console.print("Multiple agents found:")
            for i, a in enumerate(workflow.agents, 1):
                marker = " (current)" if a.name == workflow.current_agent else ""
                console.print(f"  {i}. {a.name} [{a.status.value}]{marker}")

            choice = click.prompt("Select agent", type=int, default=1)
            if 1 <= choice <= len(workflow.agents):
                agent = workflow.agents[choice - 1].name

        api.attach(workflow_id, agent)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@cli.command()
@click.argument("workflow_id")
@click.option("--agent", "-a", help="Specific agent")
@click.option("--follow", "-f", is_flag=True, help="Tail mode")
@click.option("--lines", "-n", default=100, help="Number of lines")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.pass_context
def logs(
    ctx: click.Context,
    workflow_id: str,
    agent: str | None,
    follow: bool,
    lines: int,
    output_json: bool,
) -> None:
    """View agent output history."""
    api: AgenticAPI = ctx.obj["api"]

    try:
        workflow = api.get_workflow(workflow_id)
        if workflow is None:
            console.print(f"[red]Workflow not found:[/red] {workflow_id}")
            sys.exit(1)

        if follow:
            # Tail mode
            last_output = ""
            while True:
                output = api.get_agent_output(workflow_id, agent, lines)
                if output != last_output:
                    # Print new content
                    if last_output:
                        new_lines = output[len(last_output):]
                        click.echo(new_lines, nl=False)
                    else:
                        click.echo(output, nl=False)
                    last_output = output
                time.sleep(0.5)
        else:
            output = api.get_agent_output(workflow_id, agent, lines)
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
    """Stop workflow and kill all agents."""
    api: AgenticAPI = ctx.obj["api"]

    try:
        stopped = api.stop(workflow_id)

        if output_json:
            click.echo(json.dumps({"ok": True, "stopped": stopped}))
        else:
            if stopped:
                console.print(f"[green]Stopped agents:[/green] {', '.join(stopped)}")
            else:
                console.print("[dim]No agents to stop[/dim]")
    except ValueError as e:
        if output_json:
            click.echo(json.dumps({"ok": False, "error": str(e)}))
        else:
            console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@cli.command()
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
    """Send message to running agent."""
    api: AgenticAPI = ctx.obj["api"]

    success = api.send_message(workflow_id, message, agent)

    if output_json:
        click.echo(json.dumps({"ok": success}))
    else:
        if success:
            console.print("[green]Message sent[/green]")
        else:
            console.print("[red]Failed to send message[/red]")
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
            console.print(Panel(
                _workflow_details(workflow),
                title=f"Workflow: {workflow.id}",
                border_style="cyan",
            ))
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


def _workflow_details(workflow) -> str:
    """Format workflow details for display."""
    lines = []
    lines.append(f"[bold]Name:[/bold] {workflow.name}")
    lines.append(f"[bold]Status:[/bold] [{_status_color(workflow.status)}]{workflow.status.value}[/{_status_color(workflow.status)}]")
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


@cli.command()
def tui() -> None:
    """Launch interactive TUI for workflow management."""
    from .tui import main as tui_main
    tui_main()


if __name__ == "__main__":
    cli()
