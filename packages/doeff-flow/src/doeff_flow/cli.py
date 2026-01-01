"""
CLI for observing live effect traces.

Commands:
    watch   - Watch live effect trace for a workflow (or all workflows)
    ps      - List active workflows
    history - Show execution history for a workflow
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import click
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from doeff_flow.trace import get_default_trace_dir, validate_workflow_id

console = Console()


@click.group()
def cli():
    """doeff-flow CLI - Live workflow trace observability."""


@cli.command()
@click.argument("workflow_id", required=False)
@click.option(
    "--trace-dir",
    default=None,
    type=click.Path(path_type=Path),
    help="Directory containing trace files (default: ~/.local/state/doeff-flow)",
)
@click.option(
    "--exit-on-complete",
    is_flag=True,
    help="Exit when workflow completes or fails",
)
@click.option(
    "--poll-interval",
    default=0.1,
    type=float,
    help="Poll interval in seconds (default: 0.1)",
)
def watch(
    workflow_id: str | None,
    trace_dir: Path | None,
    exit_on_complete: bool,
    poll_interval: float,
):
    """Watch live effect trace for workflows.

    If WORKFLOW_ID is provided, watch that specific workflow.
    If omitted, watch all workflows in the trace directory.
    """
    if trace_dir is None:
        trace_dir = get_default_trace_dir()

    if workflow_id:
        # Watch single workflow
        try:
            workflow_id = validate_workflow_id(workflow_id)
        except ValueError as e:
            raise click.BadParameter(str(e)) from e
        _watch_single(trace_dir, workflow_id, exit_on_complete, poll_interval)
    else:
        # Watch all workflows
        _watch_all(trace_dir, exit_on_complete, poll_interval)


def _watch_single(
    trace_dir: Path,
    workflow_id: str,
    exit_on_complete: bool,
    poll_interval: float,
) -> None:
    """Watch a single workflow with rich live display."""
    trace_file = trace_dir / workflow_id / "trace.jsonl"
    last_line_count = 0

    # Clear terminal for clean live display
    console.clear()

    with Live(console=console, refresh_per_second=10) as live:
        while True:
            if trace_file.exists():
                lines = trace_file.read_text().strip().split("\n")
                if lines and lines[0]:
                    if len(lines) > last_line_count:
                        last_line_count = len(lines)
                        data = json.loads(lines[-1])
                        live.update(_render_trace_panel(data))

                        if exit_on_complete and data["status"] in ("completed", "failed"):
                            console.print(
                                f"\n[bold]Workflow {workflow_id} finished:[/bold] "
                                f"[{'green' if data['status'] == 'completed' else 'red'}]"
                                f"{data['status']}[/]"
                            )
                            return
            else:
                live.update(
                    Panel(
                        f"[dim]Waiting for workflow [bold]{workflow_id}[/bold] to start...[/dim]",
                        title="⏳ Waiting",
                        border_style="dim",
                    )
                )
            time.sleep(poll_interval)


def _render_trace_panel(data: dict) -> Panel:
    """Render a single workflow trace as a rich Panel with call tree."""
    from rich.console import Group

    wf_id = data["workflow_id"]
    status = data["status"]
    step = data["step"]

    # Status styling
    status_styles = {
        "running": ("▶", "yellow"),
        "pending": ("○", "dim"),
        "paused": ("⏸", "blue"),
        "completed": ("✓", "green"),
        "failed": ("✗", "red"),
    }
    icon, color = status_styles.get(status, ("?", "white"))

    # Build call stack tree (kleisli trace)
    tree = Tree(f"[bold magenta]⚡ {wf_id}[/bold magenta]")
    current = tree

    trace_frames = data.get("trace", [])
    is_failed = status == "failed"

    for i, frame in enumerate(trace_frames):
        fn = frame["function"]
        file_name = Path(frame["file"]).name
        line = frame["line"]
        code = frame.get("code")

        # Show function with location
        if code:
            # Truncate code if too long
            code_display = code.strip()
            if len(code_display) > 50:
                code_display = code_display[:47] + "..."
            label = f"[cyan bold]{fn}[/] [dim]{file_name}:{line}[/]\n[dim italic]  {code_display}[/]"
        else:
            label = f"[cyan bold]{fn}[/] [dim]{file_name}:{line}[/]"

        # Use different style for deepest frame (current position or error location)
        if i == len(trace_frames) - 1:
            if is_failed:
                label = f"[red bold]✗[/] {label}"
            else:
                label = f"[yellow]→[/] {label}"

        current = current.add(label)

    # Show "empty stack" message if no frames
    if not trace_frames:
        current.add("[dim italic]<top level>[/]")

    # Format timestamp
    updated = data["updated_at"].split("T")[1][:12] if "T" in data["updated_at"] else data["updated_at"]

    # Build content with optional error section
    content_parts = [tree]

    if is_failed and data.get("error"):
        error_text = Text()
        error_text.append("\n\n")
        error_text.append("Error: ", style="red bold")
        error_text.append(data["error"], style="red")
        content_parts.append(error_text)

    return Panel(
        Group(*content_parts),
        title=f"[{color}]{icon}[/] {wf_id} [{color}]{status}[/] step {step}",
        subtitle=f"[dim]Updated: {updated}[/dim]",
        border_style=color,
    )


def _watch_all(
    trace_dir: Path,
    exit_on_complete: bool,
    poll_interval: float,
) -> None:
    """Watch all workflows with rich live display."""
    last_states: dict[str, tuple[int, str]] = {}

    # Clear terminal for clean live display
    console.clear()

    with Live(console=console, refresh_per_second=10) as live:
        while True:
            if not trace_dir.exists():
                live.update(
                    Panel(
                        f"[dim]Waiting for workflows in [bold]{trace_dir}[/bold]...[/dim]",
                        title="⏳ Waiting",
                        border_style="dim",
                    )
                )
                time.sleep(poll_interval)
                continue

            workflows = _collect_workflow_states(trace_dir)
            if not workflows:
                live.update(
                    Panel(
                        f"[dim]Waiting for workflows in [bold]{trace_dir}[/bold]...[/dim]",
                        title="⏳ Waiting",
                        border_style="dim",
                    )
                )
                time.sleep(poll_interval)
                continue

            # Check for updates
            updated = False
            for wf_id, data in workflows.items():
                trace_file = trace_dir / wf_id / "trace.jsonl"
                lines = trace_file.read_text().strip().split("\n")
                line_count = len(lines)
                status = data["status"]

                if wf_id not in last_states or last_states[wf_id] != (line_count, status):
                    last_states[wf_id] = (line_count, status)
                    updated = True

            if updated:
                live.update(_render_all_workflows_table(workflows))

            # Check if all workflows completed
            if exit_on_complete and workflows:
                all_done = all(
                    w["status"] in ("completed", "failed") for w in workflows.values()
                )
                if all_done:
                    console.print("\n[bold]All workflows finished.[/bold]")
                    return

            time.sleep(poll_interval)


def _collect_workflow_states(trace_dir: Path) -> dict[str, dict]:
    """Collect current state of all workflows."""
    workflows = {}
    for wf_dir in sorted(trace_dir.iterdir()):
        if wf_dir.is_dir():
            trace_file = wf_dir / "trace.jsonl"
            if trace_file.exists():
                lines = trace_file.read_text().strip().split("\n")
                if lines and lines[-1]:
                    try:
                        data = json.loads(lines[-1])
                        workflows[data["workflow_id"]] = data
                    except json.JSONDecodeError:
                        continue
    return workflows


def _format_call_stack(trace: list[dict], max_len: int = 50) -> str:
    """Format call stack as a compact string: fn1 → fn2 → fn3."""
    if not trace:
        return "[dim]<top level>[/]"

    functions = [frame["function"] for frame in trace]
    # Show arrow chain: fn1 → fn2 → fn3
    chain = " → ".join(functions)

    if len(chain) > max_len:
        # Truncate from the beginning, keeping the deepest frames
        chain = "..." + chain[-(max_len - 3) :]

    return chain


def _render_all_workflows_table(workflows: dict[str, dict]) -> Panel:
    """Render a table of all workflows with call stacks."""
    # Status styling
    status_styles = {
        "running": ("▶", "yellow"),
        "pending": ("○", "dim"),
        "paused": ("⏸", "blue"),
        "completed": ("✓", "green"),
        "failed": ("✗", "red"),
    }

    table = Table(show_header=True, header_style="bold")
    table.add_column("Workflow", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("Step", justify="right")
    table.add_column("Call Stack", style="dim", max_width=50)

    for wf_id, data in sorted(workflows.items()):
        status = data["status"]
        icon, color = status_styles.get(status, ("?", "white"))
        step = str(data["step"])

        # Format call stack instead of current effect
        call_stack = _format_call_stack(data.get("trace", []))

        table.add_row(
            wf_id,
            Text(f"{icon} {status}", style=color),
            step,
            call_stack,
        )

    # Summary
    total = len(workflows)
    running = sum(1 for w in workflows.values() if w["status"] == "running")
    completed = sum(1 for w in workflows.values() if w["status"] == "completed")
    failed = sum(1 for w in workflows.values() if w["status"] == "failed")

    subtitle = (
        f"[dim]Total: {total}  "
        f"[yellow]Running: {running}[/]  "
        f"[green]Completed: {completed}[/]  "
        f"[red]Failed: {failed}[/][/dim]"
    )

    return Panel(
        table,
        title="[bold]doeff-flow Workflow Monitor[/bold]",
        subtitle=subtitle,
        border_style="blue",
    )


@cli.command()
@click.option(
    "--trace-dir",
    default=None,
    type=click.Path(path_type=Path),
    help="Directory containing trace files (default: ~/.local/state/doeff-flow)",
)
def ps(trace_dir: Path | None):
    """List active workflows."""
    if trace_dir is None:
        trace_dir = get_default_trace_dir()

    if not trace_dir.exists():
        console.print("[dim]No workflows found[/dim]")
        return

    # Status styling
    status_styles = {
        "running": ("▶", "yellow"),
        "pending": ("○", "dim"),
        "paused": ("⏸", "blue"),
        "completed": ("✓", "green"),
        "failed": ("✗", "red"),
    }

    table = Table(show_header=True, header_style="bold")
    table.add_column("Workflow ID", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("Step", justify="right")
    table.add_column("Last Updated", style="dim")

    found = False
    for wf_dir in sorted(trace_dir.iterdir()):
        if wf_dir.is_dir():
            trace_file = wf_dir / "trace.jsonl"
            if trace_file.exists():
                lines = trace_file.read_text().strip().split("\n")
                if lines and lines[-1]:
                    try:
                        data = json.loads(lines[-1])
                        status = data["status"]
                        icon, color = status_styles.get(status, ("?", "white"))

                        # Format timestamp
                        updated = data["updated_at"].split("T")[1][:8] if "T" in data["updated_at"] else data["updated_at"]

                        table.add_row(
                            data["workflow_id"],
                            Text(f"{icon} {status}", style=color),
                            str(data["step"]),
                            updated,
                        )
                        found = True
                    except json.JSONDecodeError:
                        continue

    if not found:
        console.print("[dim]No workflows found[/dim]")
    else:
        console.print(table)


@cli.command()
@click.argument("workflow_id")
@click.option(
    "--trace-dir",
    default=None,
    type=click.Path(path_type=Path),
    help="Directory containing trace files (default: ~/.local/state/doeff-flow)",
)
@click.option(
    "--last",
    default=10,
    type=int,
    help="Show last N steps (default: 10)",
)
def history(workflow_id: str, trace_dir: Path | None, last: int):
    """Show execution history for a workflow.

    WORKFLOW_ID is the unique identifier of the workflow.
    """
    if trace_dir is None:
        trace_dir = get_default_trace_dir()

    try:
        workflow_id = validate_workflow_id(workflow_id)
    except ValueError as e:
        raise click.BadParameter(str(e)) from e

    trace_file = trace_dir / workflow_id / "trace.jsonl"
    if not trace_file.exists():
        console.print(f"[red]No trace found for[/red] [bold]{workflow_id}[/bold]")
        return

    lines = trace_file.read_text().strip().split("\n")
    if not lines or not lines[0]:
        console.print(f"[dim]No trace data for {workflow_id}[/dim]")
        return

    # Status styling
    status_styles = {
        "running": ("▶", "yellow"),
        "pending": ("○", "dim"),
        "paused": ("⏸", "blue"),
        "completed": ("✓", "green"),
        "failed": ("✗", "red"),
    }

    table = Table(show_header=True, header_style="bold", title=f"History: {workflow_id}")
    table.add_column("Step", justify="right", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("Effect", style="dim", max_width=50)

    for line in lines[-last:]:
        try:
            data = json.loads(line)
            status = data["status"]
            icon, color = status_styles.get(status, ("?", "white"))

            effect = data.get("current_effect") or "-"
            if len(effect) > 50:
                effect = effect[:47] + "..."

            table.add_row(
                str(data["step"]),
                Text(f"{icon} {status}", style=color),
                effect,
            )
        except json.JSONDecodeError:
            continue

    console.print(table)


if __name__ == "__main__":
    cli()
