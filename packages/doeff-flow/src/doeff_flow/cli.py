"""
CLI for observing live effect traces.

Commands:
    watch   - Watch live effect trace for a workflow
    ps      - List active workflows
    history - Show execution history for a workflow
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import click

from doeff_flow.trace import validate_workflow_id


@click.group()
def cli():
    """doeff-flow CLI - Live workflow trace observability."""


@cli.command()
@click.argument("workflow_id")
@click.option(
    "--trace-dir",
    default=".doeff-flow",
    type=click.Path(path_type=Path),
    help="Directory containing trace files",
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
    workflow_id: str,
    trace_dir: Path,
    exit_on_complete: bool,
    poll_interval: float,
):
    """Watch live effect trace for a workflow.

    WORKFLOW_ID is the unique identifier of the workflow to watch.
    """
    try:
        workflow_id = validate_workflow_id(workflow_id)
    except ValueError as e:
        raise click.BadParameter(str(e)) from e

    trace_file = trace_dir / workflow_id / "trace.jsonl"

    last_line_count = 0
    while True:
        if trace_file.exists():
            lines = trace_file.read_text().strip().split("\n")
            if lines and lines[0]:  # Check for non-empty content
                if len(lines) > last_line_count:
                    last_line_count = len(lines)
                    data = json.loads(lines[-1])  # Read last line
                    _render_trace(data)

                    if exit_on_complete and data["status"] in ("completed", "failed"):
                        click.echo(f"\nWorkflow {workflow_id} finished: {data['status']}")
                        return
        else:
            click.echo(f"Waiting for workflow {workflow_id} to start...")
        time.sleep(poll_interval)


def _render_trace(data: dict) -> None:
    """Render trace to terminal with clear + redraw.

    Args:
        data: Trace data dictionary from JSONL.
    """
    click.clear()

    wf_id = data["workflow_id"]
    status = data["status"]
    step = data["step"]

    # Calculate dynamic width based on content
    header = f" {wf_id} [{status}] step {step} "
    box_width = max(55, len(header) + 4)

    click.echo(f"\u250c\u2500{header}\u2500" + "\u2500" * (box_width - len(header) - 3) + "\u2510")
    click.echo("\u2502" + " " * (box_width - 1) + "\u2502")

    for i, frame in enumerate(data["trace"]):
        indent = "  " * i
        fn = frame["function"]
        file_name = Path(frame["file"]).name
        loc = f"{file_name}:{frame['line']}"
        line = f"\u2502  {indent}{fn:<20} {loc}"
        # Pad to box width
        padding = box_width - len(line)
        click.echo(line + " " * padding + "\u2502")

    if data["current_effect"]:
        indent = "  " * len(data["trace"])
        effect_line = f"\u2502  {indent}\u21b3 {data['current_effect']}"
        # Truncate if too long
        if len(effect_line) > box_width - 1:
            effect_line = effect_line[: box_width - 4] + "..."
        padding = box_width - len(effect_line)
        click.echo(effect_line + " " * padding + "\u2502")

    click.echo("\u2502" + " " * (box_width - 1) + "\u2502")

    # Format timestamp for display (just time portion)
    updated = data["updated_at"].split("T")[1][:12] if "T" in data["updated_at"] else data["updated_at"]
    update_line = f"\u2502  Updated: {updated}"
    padding = box_width - len(update_line)
    click.echo(update_line + " " * padding + "\u2502")

    click.echo("\u2514" + "\u2500" * (box_width - 1) + "\u2518")


@cli.command()
@click.option(
    "--trace-dir",
    default=".doeff-flow",
    type=click.Path(path_type=Path),
    help="Directory containing trace files",
)
def ps(trace_dir: Path):
    """List active workflows."""
    if not trace_dir.exists():
        click.echo("No workflows found")
        return

    found = False
    for wf_dir in sorted(trace_dir.iterdir()):
        if wf_dir.is_dir():
            trace_file = wf_dir / "trace.jsonl"
            if trace_file.exists():
                lines = trace_file.read_text().strip().split("\n")
                if lines and lines[-1]:
                    try:
                        data = json.loads(lines[-1])
                        click.echo(f"{data['workflow_id']}\t{data['status']}\tstep {data['step']}")
                        found = True
                    except json.JSONDecodeError:
                        continue

    if not found:
        click.echo("No workflows found")


@cli.command()
@click.argument("workflow_id")
@click.option(
    "--trace-dir",
    default=".doeff-flow",
    type=click.Path(path_type=Path),
    help="Directory containing trace files",
)
@click.option(
    "--last",
    default=10,
    type=int,
    help="Show last N steps (default: 10)",
)
def history(workflow_id: str, trace_dir: Path, last: int):
    """Show execution history for a workflow.

    WORKFLOW_ID is the unique identifier of the workflow.
    """
    try:
        workflow_id = validate_workflow_id(workflow_id)
    except ValueError as e:
        raise click.BadParameter(str(e)) from e

    trace_file = trace_dir / workflow_id / "trace.jsonl"
    if not trace_file.exists():
        click.echo(f"No trace found for {workflow_id}")
        return

    lines = trace_file.read_text().strip().split("\n")
    if not lines or not lines[0]:
        click.echo(f"No trace data for {workflow_id}")
        return

    for line in lines[-last:]:
        try:
            data = json.loads(line)
            effect = data.get("current_effect") or "-"
            # Truncate effect for display
            if len(effect) > 50:
                effect = effect[:47] + "..."
            click.echo(f"step {data['step']:4d}  {data['status']:10s}  {effect}")
        except json.JSONDecodeError:
            continue


if __name__ == "__main__":
    cli()
