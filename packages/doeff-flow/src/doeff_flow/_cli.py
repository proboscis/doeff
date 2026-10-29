"""
CLI for observing live effect traces (the console script ``doeff-flow``).

Commands:
    watch   - Watch live effect trace for a workflow (or all workflows)
    ps      - List active workflows
    history - Show execution history for a workflow

Each command runs a doeff program: the program reads the traces with the ReadTraces
effect, waits between polls with doeff-time's Delay and shows a live panel with
ShowPanel. The command is the composition root that installs the handlers —
``trace_files_handler`` over ``os_file_handler`` for the trace files,
``sync_time_handler`` for the wall clock and ``_live_handler`` for the terminal.
"""

from collections import Counter
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path
from typing import Any, TypeVar

import click
from doeff_core_effects import os_file_handler
from doeff_time import Delay, sync_time_handler
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from doeff import Effect, EffectBase, Pass, Program, Resume, do, handler, run, with_handlers
from doeff.program import ProgramHandler
from doeff_flow._effects import ReadTraces
from doeff_flow._handlers.files import trace_files_handler
from doeff_flow._trace import LiveTrace, TraceFrame, get_default_trace_dir, validate_workflow_id

console = Console()

_T = TypeVar("_T")

_FINISHED = ("completed", "failed")


def _status_style(status: str) -> tuple[str, str]:
    """The icon and the color a status is shown with."""
    match status:
        case "running":
            return ("▶", "yellow")
        case "pending":
            return ("○", "dim")
        case "paused":
            return ("⏸", "blue")
        case "completed":
            return ("✓", "green")
        case "failed":
            return ("✗", "red")
        case _:
            return ("?", "white")


@dataclass(frozen=True, kw_only=True)
class ShowPanel(EffectBase[None]):
    """Show ``renderable`` as the live display's current content."""

    renderable: RenderableType


def _live_handler(live: Live):
    """Answer ShowPanel by updating ``live``."""

    @handler
    def show_on_live(effect: Effect, k: Any):
        if isinstance(effect, ShowPanel):
            live.update(effect.renderable)
            return (yield Resume(k, None))
        return (yield Pass(effect, k))

    return show_on_live


def _run(program: Program[_T, Any], *handlers: ProgramHandler) -> _T:
    """Run a command's program over the real trace files and the wall clock."""
    return run(
        with_handlers(
            [os_file_handler, sync_time_handler(), *handlers, trace_files_handler], program
        )
    )


@dataclass(frozen=True)
class _Workflow:
    """A workflow as the trace shows it: its latest entry and how many entries it wrote."""

    latest: LiveTrace
    entries: int


def _workflows(entries: tuple[LiveTrace, ...]) -> tuple[_Workflow, ...]:
    """The workflows the entries belong to, in the order they were read."""
    groups = (tuple(group) for _, group in groupby(entries, key=lambda entry: entry.workflow_id))
    return tuple(_Workflow(latest=group[-1], entries=len(group)) for group in groups)


def _waiting_panel(message: str) -> Panel:
    return Panel(f"[dim]{message}[/dim]", title="⏳ Waiting", border_style="dim")


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
@click.option(
    "--status-only",
    is_flag=True,
    help="Show only slog status updates (simplified display)",
)
def watch(
    workflow_id: str | None,
    trace_dir: Path | None,
    exit_on_complete: bool,
    poll_interval: float,
    status_only: bool,
):
    """Watch live effect trace for workflows.

    If WORKFLOW_ID is provided, watch that specific workflow.
    If omitted, watch all workflows in the trace directory.
    """
    if trace_dir is None:
        trace_dir = get_default_trace_dir()

    if workflow_id:
        try:
            workflow_id = validate_workflow_id(workflow_id)
        except ValueError as e:
            raise click.BadParameter(str(e)) from e
        program = _watch_single(
            trace_dir, workflow_id, exit_on_complete, poll_interval, status_only
        )
    else:
        program = _watch_all(trace_dir, exit_on_complete, poll_interval)

    # Clear terminal for clean live display
    console.clear()
    with Live(console=console, refresh_per_second=10) as live:
        finished = _run(program, _live_handler(live))
        if workflow_id and finished is not None:
            color = "green" if finished.status == "completed" else "red"
            console.print(
                f"\n[bold]Workflow {workflow_id} finished:[/bold] [{color}]{finished.status}[/]"
            )
        elif finished is not None:
            console.print("\n[bold]All workflows finished.[/bold]")


@do
def _watch_single(
    trace_dir: Path,
    workflow_id: str,
    exit_on_complete: bool,
    poll_interval: float,
    status_only: bool = False,
):
    """Show a single workflow's latest entry whenever it writes one.

    Returns the finished entry when ``exit_on_complete`` and the workflow completed or
    failed; polls until interrupted otherwise.
    """
    shown = 0
    while True:
        entries = yield ReadTraces(trace_dir=trace_dir, workflow_id=workflow_id)
        if not entries:
            yield ShowPanel(
                renderable=_waiting_panel(
                    f"Waiting for workflow [bold]{workflow_id}[/bold] to start..."
                )
            )
        elif len(entries) > shown:
            shown = len(entries)
            latest = entries[-1]
            yield ShowPanel(renderable=_render_trace_panel(latest, status_only=status_only))
            if exit_on_complete and latest.status in _FINISHED:
                return latest
        yield Delay(poll_interval)


@do
def _watch_all(trace_dir: Path, exit_on_complete: bool, poll_interval: float):
    """Show the table of all workflows whenever one of them changes.

    Returns the workflows' latest entries when ``exit_on_complete`` and all of them
    completed or failed; polls until interrupted otherwise.
    """
    shown: tuple[tuple[str, int, str], ...] = ()
    while True:
        workflows = _workflows((yield ReadTraces(trace_dir=trace_dir)))
        if not workflows:
            yield ShowPanel(
                renderable=_waiting_panel(f"Waiting for workflows in [bold]{trace_dir}[/bold]...")
            )
        else:
            latests = tuple(workflow.latest for workflow in workflows)
            seen = tuple(
                (workflow.latest.workflow_id, workflow.entries, workflow.latest.status)
                for workflow in workflows
            )
            if seen != shown:
                shown = seen
                yield ShowPanel(renderable=_render_all_workflows_table(latests))
            if exit_on_complete and all(latest.status in _FINISHED for latest in latests):
                return latests
        yield Delay(poll_interval)


def _frame_label(frame: TraceFrame) -> str:
    """One call-tree line: the function with its location, and its code when known."""
    location = f"[cyan bold]{frame.function}[/] [dim]{Path(frame.file).name}:{frame.line}[/]"
    if not frame.code:
        return location
    # Truncate code if too long
    code_display = frame.code.strip()
    if len(code_display) > 50:
        code_display = code_display[:47] + "..."
    return f"{location}\n[dim italic]  {code_display}[/]"


def _slog_text(last_slog: dict, suffix: str) -> Text:
    """The slog payload shown as ``[status] msg`` (or its repr when it has no status)."""
    slog_status = last_slog.get("status", "")
    slog_msg = last_slog.get("msg", str(last_slog))
    text = Text()
    if slog_status:
        text.append(f"[{slog_status}]", style="bold cyan")
        text.append(f" {slog_msg}{suffix}")
    else:
        text.append(f"{last_slog}{suffix}")
    return text


def _render_trace_panel(data: LiveTrace, status_only: bool = False) -> Panel:
    """Render a single workflow trace as a rich Panel with call tree.

    Args:
        data: The workflow's latest trace entry.
        status_only: If True, show only slog status updates (simplified display).
    """
    icon, color = _status_style(data.status)
    is_failed = data.status == "failed"
    is_completed = data.status == "completed"

    # Build call stack tree (kleisli trace)
    tree = Tree(f"[bold magenta]⚡ {data.workflow_id}[/bold magenta]")
    current = tree
    for i, frame in enumerate(data.trace):
        label = _frame_label(frame)
        # Use different style for deepest frame (current position or error location)
        if i == len(data.trace) - 1:
            label = f"[red bold]✗[/] {label}" if is_failed else f"[yellow]→[/] {label}"
        current = current.add(label)

    # Show gather info as parallel branches if inside a Gather
    gather = data.gather
    if gather is not None and gather.total > 0:
        gather_node = current.add(
            f"[blue bold]⋔ gather[/] [dim]({gather.completed}/{gather.total} done)[/]"
        )
        # Show completed tasks with results
        for j, result in enumerate(gather.results):
            result_display = result[:30] + "..." if len(result) > 30 else result
            gather_node.add(f"[green]✓[/] [dim]task {j + 1}:[/] {result_display}")
        # Show current task (running)
        if gather.completed < gather.total:
            gather_node.add(
                f"[yellow]→[/] [dim]task {gather.completed + 1}:[/] [yellow]running...[/]"
            )
        # Show remaining tasks
        remaining = gather.total - gather.completed - 1
        if remaining > 0:
            gather_node.add(f"[dim]○ {remaining} more pending[/]")

    # Show "empty stack" message if no frames
    if not data.trace and gather is None:
        current.add("[dim italic]<top level>[/]")

    # Format timestamp
    updated = data.updated_at.split("T")[1][:12] if "T" in data.updated_at else data.updated_at
    title = f"[{color}]{icon}[/] {data.workflow_id} [{color}]{data.status}[/] step {data.step}"
    subtitle = f"[dim]Updated: {updated}[/dim]"

    # For status_only mode, show simplified display
    if status_only:
        body = (
            _slog_text(data.last_slog, "")
            if data.last_slog
            else Text(f"Step {data.step}: {data.current_effect or '-'}", style="dim")
        )
        return Panel(body, title=title, subtitle=subtitle, border_style=color)

    # Show slog status prominently at the top if available
    slog_part = (_slog_text(data.last_slog, "\n\n"),) if data.last_slog else ()

    # Show result if completed
    result_part: tuple[Text, ...] = ()
    if is_completed and data.result:
        result_display = data.result
        if len(result_display) > 100:
            result_display = result_display[:97] + "..."
        result_part = (
            Text.assemble("\n\n", ("Result: ", "green bold"), (result_display, "green")),
        )

    # Show error if failed
    error_part: tuple[Text, ...] = ()
    if is_failed and data.error:
        error_part = (Text.assemble("\n\n", ("Error: ", "red bold"), (data.error, "red")),)

    return Panel(
        Group(*slog_part, tree, *result_part, *error_part),
        title=title,
        subtitle=subtitle,
        border_style=color,
    )


def _format_call_stack(trace: list[TraceFrame], max_len: int = 50) -> str:
    """Format call stack as a compact string: fn1 → fn2 → fn3."""
    if not trace:
        return "[dim]<top level>[/]"

    # Show arrow chain: fn1 → fn2 → fn3
    chain = " → ".join(frame.function for frame in trace)

    if len(chain) > max_len:
        # Truncate from the beginning, keeping the deepest frames
        chain = "..." + chain[-(max_len - 3) :]

    return chain


def _render_all_workflows_table(workflows: tuple[LiveTrace, ...]) -> Panel:
    """Render a table of all workflows with call stacks."""
    table = Table(show_header=True, header_style="bold")
    table.add_column("Workflow", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("Step", justify="right")
    table.add_column("Result / Error", max_width=40)

    for data in sorted(workflows, key=lambda data: data.workflow_id):
        icon, color = _status_style(data.status)

        # Show result for completed, error for failed, call stack for running
        if data.status == "completed" and data.result:
            result_display = data.result
            if len(result_display) > 37:
                result_display = result_display[:34] + "..."
            result_cell = Text(result_display, style="green")
        elif data.status == "failed" and data.error:
            error_display = data.error
            if len(error_display) > 37:
                error_display = error_display[:34] + "..."
            result_cell = Text(error_display, style="red")
        else:
            # Show call stack for running workflows
            result_cell = Text(_format_call_stack(data.trace), style="dim")

        table.add_row(
            data.workflow_id,
            Text(f"{icon} {data.status}", style=color),
            str(data.step),
            result_cell,
        )

    # Summary
    statuses = Counter(data.status for data in workflows)
    subtitle = (
        f"[dim]Total: {len(workflows)}  "
        f"[yellow]Running: {statuses['running']}[/]  "
        f"[green]Completed: {statuses['completed']}[/]  "
        f"[red]Failed: {statuses['failed']}[/][/dim]"
    )

    return Panel(
        table,
        title="[bold]doeff-flow Workflow Monitor[/bold]",
        subtitle=subtitle,
        border_style="blue",
    )


@do
def _ps_table(trace_dir: Path):
    """The table of each workflow's latest entry, or None when there is no workflow."""
    workflows = _workflows((yield ReadTraces(trace_dir=trace_dir)))
    if not workflows:
        return None

    table = Table(show_header=True, header_style="bold")
    table.add_column("Workflow ID", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("Step", justify="right")
    table.add_column("Last Updated", style="dim")

    for data in (workflow.latest for workflow in workflows):
        icon, color = _status_style(data.status)
        # Format timestamp
        updated = data.updated_at.split("T")[1][:8] if "T" in data.updated_at else data.updated_at
        table.add_row(
            data.workflow_id,
            Text(f"{icon} {data.status}", style=color),
            str(data.step),
            updated,
        )
    return table


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

    table = _run(_ps_table(trace_dir))
    if table is None:
        console.print("[dim]No workflows found[/dim]")
    else:
        console.print(table)


@do
def _history_table(trace_dir: Path, workflow_id: str, last: int):
    """The table of a workflow's last ``last`` entries, or None when it has no trace."""
    entries = yield ReadTraces(trace_dir=trace_dir, workflow_id=workflow_id)
    if not entries:
        return None

    table = Table(show_header=True, header_style="bold", title=f"History: {workflow_id}")
    table.add_column("Step", justify="right", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("Effect", style="dim", max_width=50)

    for data in entries[-last:]:
        icon, color = _status_style(data.status)
        effect = data.current_effect or "-"
        if len(effect) > 50:
            effect = effect[:47] + "..."
        table.add_row(str(data.step), Text(f"{icon} {data.status}", style=color), effect)
    return table


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

    table = _run(_history_table(trace_dir, workflow_id, last))
    if table is None:
        console.print(f"[red]No trace found for[/red] [bold]{workflow_id}[/bold]")
    else:
        console.print(table)


if __name__ == "__main__":
    cli()
