"""
CLI for doeff-conductor workflow orchestration.

Commands:
    run         Execute a workflow template or file
    ps          List running workflows
    show        Show workflow details
    watch       Monitor workflow progress
    attach      Attach to agent session
    logs        View session logs
    stop        Stop workflow
    issue       Issue management (create, list, show, resolve)
    workspace   Workspace management (list, cleanup)
    template    Template management (list, show, new)
"""


import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .types import IssueStatus, WorkflowStatus

console = Console()
_CLI_USER_ERROR_TYPES: tuple[type[BaseException], ...] = (
    FileNotFoundError,
    OSError,
    ValueError,
    json.JSONDecodeError,
)


def _format_duration(dt: datetime) -> str:
    """Format a datetime as relative duration."""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    seconds = int(delta.total_seconds())

    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _status_color(status: WorkflowStatus | IssueStatus | str) -> str:
    """Get Rich color for a status."""
    if isinstance(status, WorkflowStatus):
        return {
            WorkflowStatus.PENDING: "dim",
            WorkflowStatus.RUNNING: "green",
            WorkflowStatus.BLOCKED: "yellow",
            WorkflowStatus.DONE: "blue",
            WorkflowStatus.ERROR: "red",
            WorkflowStatus.ABORTED: "magenta",
        }.get(status, "white")
    if isinstance(status, IssueStatus):
        return {
            IssueStatus.OPEN: "green",
            IssueStatus.IN_PROGRESS: "yellow",
            IssueStatus.RESOLVED: "blue",
            IssueStatus.CLOSED: "dim",
        }.get(status, "white")
    return "white"


# =============================================================================
# Main CLI Group
# =============================================================================


@click.group()
@click.option("--state-dir", envvar="CONDUCTOR_STATE_DIR", help="State directory")
@click.pass_context
def cli(ctx: click.Context, state_dir: str | None) -> None:
    """conductor: Multi-agent workflow orchestration."""
    ctx.ensure_object(dict)
    ctx.obj["state_dir"] = state_dir


# =============================================================================
# Workflow Commands
# =============================================================================


@cli.command("plan")
@click.argument("workflow_file")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.option(
    "--supervision",
    type=click.Choice(["autonomous", "phase-checkpoints"]),
    default="autonomous",
    show_default=True,
    help="Run-scoped supervision policy for the approval artifact",
)
def plan_cmd(workflow_file: str, output_json: bool, supervision: str) -> None:
    """Produce the overseer binding plan for a workflow."""
    from doeff_conductor.verbs import plan_workflow
    from doeff_conductor.workflow_loader import load_workflow_spec

    try:
        workflow = load_workflow_spec(workflow_file)
        plan = plan_workflow(workflow, supervision=supervision)
        if output_json:
            click.echo(json.dumps(plan.to_dict(), indent=2))
            return

        table = Table(show_header=True, header_style="bold")
        table.add_column("NODE", style="cyan")
        table.add_column("PHASE")
        table.add_column("ROLE")
        table.add_column("PROFILE")
        table.add_column("CLASS")
        table.add_column("BUDGET")
        table.add_column("FINGERPRINT")
        for row in plan.rows:
            table.add_row(
                row.node_id,
                row.phase or "-",
                row.role,
                row.profile,
                row.verification_class,
                str(row.estimated_budget_units),
                row.fingerprint[:12],
            )
        console.print(table)
        console.print(f"Estimated budget units: {plan.estimated_budget_units}")
        console.print(f"Capabilities satisfied: {plan.capabilities_satisfied}")
        if plan.missing_capabilities:
            console.print(f"Missing capabilities: {', '.join(plan.missing_capabilities)}")
    except _CLI_USER_ERROR_TYPES as e:
        if output_json:
            click.echo(json.dumps({"error": str(e)}))
        else:
            console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@cli.command("validate")
@click.argument("workflow_file")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.option(
    "--scenario",
    "scenarios",
    multiple=True,
    help="Scenario to run; defaults to the built-in C6 suite",
)
@click.option("--run-id", help="Persist overseer state under this workflow id")
@click.option(
    "--supervision",
    type=click.Choice(["autonomous", "phase-checkpoints"]),
    default="autonomous",
    show_default=True,
    help="Run-scoped supervision policy consumed by validation launch",
)
@click.pass_context
def validate_cmd(
    ctx: click.Context,
    workflow_file: str,
    output_json: bool,
    scenarios: tuple[str, ...],
    run_id: str | None,
    supervision: str,
) -> None:
    """Validate workflow control flow under scenario-driven stubs."""
    from doeff_conductor.verbs import validate_workflow
    from doeff_conductor.workflow_loader import load_workflow_spec

    try:
        workflow = load_workflow_spec(workflow_file)
        state_dir_for_run = None
        if run_id:
            from doeff_conductor.api import ConductorAPI

            state_dir_for_run = str(ConductorAPI(ctx.obj.get("state_dir")).state_dir)
        report = validate_workflow(
            workflow,
            scenarios=scenarios or None,
            supervision=supervision,
            state_dir=state_dir_for_run,
            run_id=run_id,
        )
        if output_json:
            click.echo(json.dumps(report.to_dict(), indent=2))
            return

        table = Table(show_header=True, header_style="bold")
        table.add_column("SCENARIO", style="cyan")
        table.add_column("TERMINALS")
        table.add_column("OPEN GATES")
        for scenario_report in report.scenarios:
            table.add_row(
                scenario_report.scenario,
                str(len(scenario_report.terminals)),
                str(len(scenario_report.open_gates)),
            )
        console.print(table)
        console.print("Closure: ok")
        if run_id:
            console.print(f"Persisted overseer state for run: {run_id}")
    except _CLI_USER_ERROR_TYPES as e:
        if output_json:
            click.echo(json.dumps({"error": str(e)}))
        else:
            console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@cli.command()
@click.argument("template_or_file")
@click.option("--issue", "-i", type=click.Path(exists=True), help="Issue file")
@click.option("--params", "-p", help="Parameters as JSON")
@click.option("--run-id", help="Use a stable workflow id for replay/resume measurements")
@click.option(
    "--agent-mode",
    type=click.Choice(["agentd", "codex-exec"]),
    default="agentd",
    envvar="CONDUCTOR_AGENT_MODE",
    show_default=True,
    show_envvar=True,
    help="Agent backend to use for production agent effects",
)
@click.option("--watch", "-w", is_flag=True, help="Watch workflow progress")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.pass_context
def run(
    ctx: click.Context,
    template_or_file: str,
    issue: str | None,
    params: str | None,
    run_id: str | None,
    agent_mode: str,
    watch: bool,
    output_json: bool,
) -> None:
    """Run a workflow template or file.

    Examples:
        conductor run basic_pr --issue ISSUE-001.md
        conductor run enforced_pr --issue /path/to/issue.md --watch
        conductor run ./my_workflow.py --params '{"key": "value"}'
    """
    from .api import ConductorAPI

    api = ConductorAPI(ctx.obj.get("state_dir"))

    try:
        # Parse parameters
        parsed_params = {}
        if params:
            parsed_params = json.loads(params)

        # Load issue if provided
        issue_obj = None
        if issue:
            from .handlers.issue_handler import IssueHandler

            handler = IssueHandler()
            issue_path = Path(issue)
            if issue_path.exists():
                content = issue_path.read_text()
                from .handlers.issue_handler import _parse_frontmatter

                frontmatter, _body = _parse_frontmatter(content)
                from .effects.issue import GetIssue

                if frontmatter.get("id"):
                    issue_obj = handler.handle_get_issue(
                        GetIssue(id=frontmatter["id"])
                    )

        # Run workflow
        workflow = api.run_workflow(
            template_or_file,
            issue=issue_obj,
            params=parsed_params,
            run_id=run_id,
            agent_backend=agent_mode,
        )

        if output_json:
            click.echo(json.dumps(workflow.to_dict(), indent=2))
        else:
            console.print(f"[green]Started workflow:[/green] {workflow.id}")
            console.print(f"  Template: {workflow.template or template_or_file}")
            if workflow.issue_id:
                console.print(f"  Issue: {workflow.issue_id}")

        if watch:
            # Watch workflow
            ctx.invoke(watch_cmd, workflow_id=workflow.id)

    except _CLI_USER_ERROR_TYPES as e:
        if output_json:
            click.echo(json.dumps({"error": str(e)}))
        else:
            console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@cli.command("ps")
@click.option("--status", "-s", multiple=True, help="Filter by status")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.pass_context
def ps_cmd(
    ctx: click.Context,
    status: tuple[str, ...],
    output_json: bool,
) -> None:
    """List running workflows.

    Example:
        conductor ps
        conductor ps --status running --status blocked
    """
    from .api import ConductorAPI

    api = ConductorAPI(ctx.obj.get("state_dir"))

    workflows = api.list_workflows(
        status=[WorkflowStatus(s) for s in status] if status else None
    )

    if output_json:
        click.echo(json.dumps([w.to_dict() for w in workflows], indent=2))
        return

    if not workflows:
        console.print("[dim]No workflows found[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="cyan")
    table.add_column("NAME")
    table.add_column("STATUS")
    table.add_column("TEMPLATE")
    table.add_column("UPDATED")

    for wf in workflows:
        status_style = _status_color(wf.status)
        table.add_row(
            wf.id[:7],
            wf.name,
            Text(wf.status.value, style=status_style),
            wf.template or "-",
            _format_duration(wf.updated_at),
        )

    console.print(table)


def _show_progress_since(
    ctx: click.Context,
    workflow_id: str,
    output_json: bool,
    since: int,
) -> None:
    from doeff_conductor.api import ConductorAPI
    from doeff_conductor.overseer import progress_since

    try:
        state_dir = ConductorAPI(ctx.obj.get("state_dir")).state_dir
        events = progress_since(state_dir, workflow_id, since)
        if output_json:
            click.echo(json.dumps(events, indent=2))
            return

        table = Table(show_header=True, header_style="bold")
        table.add_column("SEQ", style="cyan")
        table.add_column("STATUS")
        table.add_column("PHASE")
        table.add_column("NODE")
        table.add_column("MESSAGE")
        for event in events:
            table.add_row(
                str(event["sequence"]),
                event["status"],
                event["phase"] or "-",
                event["node_id"],
                event["message"],
            )
        console.print(table)
    except _CLI_USER_ERROR_TYPES as e:
        if output_json:
            click.echo(json.dumps({"error": str(e)}))
        else:
            console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


def _append_optional_line(lines: list[str], condition: bool, line: str) -> None:
    if condition:
        lines.append(line)


def _workflow_detail_panel(workflow: object) -> Panel:
    lines: list[str] = []
    workflow_vars = vars(workflow)
    workflow_status = workflow_vars["status"]
    status_color = _status_color(workflow_status)
    lines.append(f"[bold]ID:[/bold] {workflow_vars['id']}")
    lines.append(f"[bold]Name:[/bold] {workflow_vars['name']}")
    lines.append(
        f"[bold]Status:[/bold] [{status_color}]{workflow_status.value}[/{status_color}]"
    )
    _append_optional_line(
        lines,
        workflow_vars["template"] is not None,
        f"[bold]Template:[/bold] {workflow_vars['template']}",
    )
    _append_optional_line(
        lines,
        workflow_vars["issue_id"] is not None,
        f"[bold]Issue:[/bold] {workflow_vars['issue_id']}",
    )
    lines.append(f"[bold]Created:[/bold] {workflow_vars['created_at'].isoformat()}")
    lines.append(f"[bold]Updated:[/bold] {workflow_vars['updated_at'].isoformat()}")
    _append_optional_line(
        lines,
        bool(workflow_vars["workspaces"]),
        f"\n[bold]Workspaces:[/bold] {', '.join(workflow_vars['workspaces'])}",
    )
    _append_optional_line(
        lines,
        bool(workflow_vars["agents"]),
        f"[bold]Agents:[/bold] {', '.join(workflow_vars['agents'])}",
    )
    _append_optional_line(
        lines,
        workflow_vars["pr_url"] is not None,
        f"\n[bold]PR:[/bold] {workflow_vars['pr_url']}",
    )
    _append_optional_line(
        lines,
        workflow_vars["error"] is not None,
        f"\n[red]Error:[/red] {workflow_vars['error']}",
    )
    return Panel("\n".join(lines), title=f"Workflow: {workflow_vars['id']}")


def _show_workflow_details(
    ctx: click.Context,
    workflow_id: str,
    output_json: bool,
) -> None:
    from .api import ConductorAPI

    api = ConductorAPI(ctx.obj.get("state_dir"))

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
            return

        console.print(_workflow_detail_panel(workflow))
    except _CLI_USER_ERROR_TYPES as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@cli.command("show")
@click.argument("workflow_id")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.option("--since", type=int, help="Show progress events after this sequence")
@click.pass_context
def show_cmd(
    ctx: click.Context,
    workflow_id: str,
    output_json: bool,
    since: int | None,
) -> None:
    """Show workflow details."""
    if since is not None:
        _show_progress_since(ctx, workflow_id, output_json, since)
        return

    _show_workflow_details(ctx, workflow_id, output_json)


@cli.command("watch")
@click.argument("workflow_id")
@click.option("--json", "output_json", is_flag=True, help="Output as JSONL")
@click.pass_context
def watch_cmd(
    ctx: click.Context,
    workflow_id: str,
    output_json: bool,
) -> None:
    """Watch workflow progress in real-time."""
    from .api import ConductorAPI

    api = ConductorAPI(ctx.obj.get("state_dir"))

    try:
        for update in api.watch_workflow(workflow_id):
            if output_json:
                click.echo(json.dumps(update))
            else:
                status_style = _status_color(WorkflowStatus(update["status"]))
                console.print(
                    f"[{status_style}]{update['status']}[/{status_style}] "
                    f"{update.get('message', '')}"
                )

            # Exit on terminal status
            if update.get("terminal"):
                break

    except KeyboardInterrupt:
        console.print("\n[dim]Watch stopped[/dim]")
    except _CLI_USER_ERROR_TYPES as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@cli.command("stop")
@click.argument("workflow_id")
@click.option("--agent", "-a", help="Stop specific agent only")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.pass_context
def stop_cmd(
    ctx: click.Context,
    workflow_id: str,
    agent: str | None,
    output_json: bool,
) -> None:
    """Stop a workflow or specific agent."""
    from .api import ConductorAPI

    api = ConductorAPI(ctx.obj.get("state_dir"))

    try:
        stopped = api.stop_workflow(workflow_id, agent=agent)

        if output_json:
            click.echo(json.dumps({"ok": True, "stopped": stopped}))
        elif stopped:
            console.print(f"[green]Stopped:[/green] {', '.join(stopped)}")
        else:
            console.print("[dim]Nothing to stop[/dim]")

    except _CLI_USER_ERROR_TYPES as e:
        if output_json:
            click.echo(json.dumps({"ok": False, "error": str(e)}))
        else:
            console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


# =============================================================================
# Environment Commands
# =============================================================================


@cli.group("env")
def env_group() -> None:
    """Author-facing environment vocabulary."""


@env_group.command("describe")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
def env_describe(output_json: bool) -> None:
    """Describe profiles, role conventions, router policy, and capabilities."""
    from doeff_conductor.verbs import describe_environment

    try:
        description = describe_environment()
        if output_json:
            click.echo(json.dumps(description, indent=2))
            return

        profile_table = Table(show_header=True, header_style="bold")
        profile_table.add_column("PROFILE", style="cyan")
        profile_table.add_column("BUDGET")
        profile_table.add_column("CAPABILITIES")
        for profile in description["profiles"]:
            profile_table.add_row(
                profile["name"],
                str(profile["budget_units"]),
                ", ".join(profile["capabilities"]),
            )
        console.print(profile_table)

        router_table = Table(show_header=True, header_style="bold")
        router_table.add_column("CLASS", style="cyan")
        router_table.add_column("DEFAULT PROFILE")
        for verification_class, profile in description["router_default_policy"].items():
            router_table.add_row(verification_class, profile)
        console.print(router_table)
        console.print(
            f"Available capabilities: {', '.join(description['available_capabilities'])}"
        )
    except _CLI_USER_ERROR_TYPES as e:
        if output_json:
            click.echo(json.dumps({"error": str(e)}))
        else:
            console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


# =============================================================================
# Gate Queue Commands
# =============================================================================


@cli.group("gate")
def gate_group() -> None:
    """Overseer gate queue commands."""


@gate_group.command("list")
@click.argument("workflow_id", required=False)
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.pass_context
def gate_list(ctx: click.Context, workflow_id: str | None, output_json: bool) -> None:
    """List open gates with stakes metadata and closure-preserving options."""
    from doeff_conductor.api import ConductorAPI
    from doeff_conductor.overseer import list_open_gates

    try:
        state_dir = ConductorAPI(ctx.obj.get("state_dir")).state_dir
        gates = list_open_gates(state_dir, workflow_id)
        if output_json:
            click.echo(json.dumps(gates, indent=2))
            return

        if not gates:
            console.print("[dim]No open gates found[/dim]")
            return

        table = Table(show_header=True, header_style="bold")
        table.add_column("GATE", style="cyan")
        table.add_column("WORKFLOW")
        table.add_column("PHASE")
        table.add_column("REASON")
        table.add_column("OPTIONS")
        for gate in gates:
            table.add_row(
                gate["gate_id"],
                gate["workflow_id"],
                gate["phase"] or "-",
                gate["reason"],
                ", ".join(option["name"] for option in gate["options"]),
            )
        console.print(table)
    except _CLI_USER_ERROR_TYPES as e:
        if output_json:
            click.echo(json.dumps({"error": str(e)}))
        else:
            console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


# =============================================================================
# Issue Commands
# =============================================================================


@cli.group()
def issue() -> None:
    """Issue management commands."""


@issue.command("create")
@click.argument("title")
@click.option("--body", "-b", help="Issue body (string or @file)")
@click.option("--labels", "-l", multiple=True, help="Labels")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.pass_context
def issue_create(
    ctx: click.Context,
    title: str,
    body: str | None,
    labels: tuple[str, ...],
    output_json: bool,
) -> None:
    """Create a new issue.

    Examples:
        conductor issue create "Add login feature"
        conductor issue create "Fix bug" --body "Description here"
        conductor issue create "Feature" --body @description.md --labels feature,urgent
    """
    from .effects.issue import CreateIssue
    from .handlers.issue_handler import IssueHandler

    handler = IssueHandler()

    # Handle body from file
    body_text = ""
    if body:
        body_text = Path(body[1:]).read_text() if body.startswith("@") else body

    try:
        issue_obj = handler.handle_create_issue(
            CreateIssue(
                title=title,
                body=body_text,
                labels=labels,
            )
        )

        if output_json:
            click.echo(json.dumps(issue_obj.to_dict(), indent=2))
        else:
            console.print(f"[green]Created issue:[/green] {issue_obj.id}")
            console.print(f"  Title: {issue_obj.title}")
            if labels:
                console.print(f"  Labels: {', '.join(labels)}")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@issue.command("list")
@click.option("--status", "-s", help="Filter by status (open, in_progress, resolved, closed)")
@click.option("--labels", "-l", multiple=True, help="Filter by labels")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.pass_context
def issue_list(
    ctx: click.Context,
    status: str | None,
    labels: tuple[str, ...],
    output_json: bool,
) -> None:
    """List issues."""
    from .effects.issue import ListIssues
    from .handlers.issue_handler import IssueHandler

    handler = IssueHandler()

    status_filter = IssueStatus(status) if status else None

    try:
        issues = handler.handle_list_issues(
            ListIssues(
                status=status_filter,
                labels=labels,
            )
        )

        if output_json:
            click.echo(json.dumps([i.to_dict() for i in issues], indent=2))
            return

        if not issues:
            console.print("[dim]No issues found[/dim]")
            return

        table = Table(show_header=True, header_style="bold")
        table.add_column("ID", style="cyan")
        table.add_column("TITLE")
        table.add_column("STATUS")
        table.add_column("LABELS")
        table.add_column("CREATED")

        for issue_obj in issues:
            status_style = _status_color(issue_obj.status)
            table.add_row(
                issue_obj.id,
                issue_obj.title[:40] + ("..." if len(issue_obj.title) > 40 else ""),
                Text(issue_obj.status.value, style=status_style),
                ", ".join(issue_obj.labels) or "-",
                _format_duration(issue_obj.created_at),
            )

        console.print(table)

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@issue.command("show")
@click.argument("issue_id")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.pass_context
def issue_show(
    ctx: click.Context,
    issue_id: str,
    output_json: bool,
) -> None:
    """Show issue details."""
    from .effects.issue import GetIssue
    from .handlers.issue_handler import IssueHandler

    handler = IssueHandler()

    try:
        issue_obj = handler.handle_get_issue(GetIssue(id=issue_id))

        if output_json:
            click.echo(json.dumps(issue_obj.to_dict(), indent=2))
        else:
            lines = []
            lines.append(f"[bold]ID:[/bold] {issue_obj.id}")
            lines.append(f"[bold]Title:[/bold] {issue_obj.title}")
            lines.append(
                f"[bold]Status:[/bold] [{_status_color(issue_obj.status)}]{issue_obj.status.value}[/{_status_color(issue_obj.status)}]"
            )
            if issue_obj.labels:
                lines.append(f"[bold]Labels:[/bold] {', '.join(issue_obj.labels)}")
            lines.append(f"[bold]Created:[/bold] {issue_obj.created_at.isoformat()}")
            if issue_obj.pr_url:
                lines.append(f"[bold]PR:[/bold] {issue_obj.pr_url}")
            lines.append(f"\n[bold]Body:[/bold]\n{issue_obj.body}")

            console.print(Panel("\n".join(lines), title=f"Issue: {issue_obj.id}"))

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@issue.command("resolve")
@click.argument("issue_id")
@click.option("--pr", help="Associated PR URL")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.pass_context
def issue_resolve(
    ctx: click.Context,
    issue_id: str,
    pr: str | None,
    output_json: bool,
) -> None:
    """Mark an issue as resolved."""
    from .effects.issue import GetIssue, ResolveIssue
    from .handlers.issue_handler import IssueHandler

    handler = IssueHandler()

    try:
        issue_obj = handler.handle_get_issue(GetIssue(id=issue_id))
        resolved = handler.handle_resolve_issue(
            ResolveIssue(issue=issue_obj, pr_url=pr)
        )

        if output_json:
            click.echo(json.dumps(resolved.to_dict(), indent=2))
        else:
            console.print(f"[green]Resolved:[/green] {issue_id}")
            if pr:
                console.print(f"  PR: {pr}")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


# =============================================================================
# Workspace Commands
# =============================================================================


@cli.group()
def workspace() -> None:
    """Workspace management commands."""


@workspace.command("list")
@click.option("--workflow", "-w", help="Filter by workflow ID")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.pass_context
def workspace_list(
    ctx: click.Context,
    workflow: str | None,
    output_json: bool,
) -> None:
    """List materialized workspaces."""
    from .api import ConductorAPI

    api = ConductorAPI(ctx.obj.get("state_dir"))

    try:
        workspaces = api.list_workspaces(workflow_id=workflow)

        if output_json:
            click.echo(json.dumps([item.to_dict() for item in workspaces], indent=2))
            return

        if not workspaces:
            console.print("[dim]No workspaces found[/dim]")
            return

        table = Table(show_header=True, header_style="bold")
        table.add_column("ID", style="cyan")
        table.add_column("REPO")
        table.add_column("REF")
        table.add_column("ISSUE")
        table.add_column("CREATED")

        for workspace_obj in workspaces:
            table.add_row(
                workspace_obj.id[:12],
                workspace_obj.repo,
                workspace_obj.ref,
                workspace_obj.issue_id or "-",
                _format_duration(workspace_obj.created_at),
            )

        console.print(table)

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@workspace.command("cleanup")
@click.option("--dry-run", is_flag=True, help="Show what would be cleaned up")
@click.option("--older-than", type=int, help="Only cleanup workspaces older than N days")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.pass_context
def workspace_cleanup(
    ctx: click.Context,
    dry_run: bool,
    older_than: int | None,
    output_json: bool,
) -> None:
    """Cleanup orphaned workspace materializations."""
    from .api import ConductorAPI

    api = ConductorAPI(ctx.obj.get("state_dir"))

    try:
        cleaned = api.cleanup_workspaces(
            dry_run=dry_run,
            older_than_days=older_than,
        )

        if output_json:
            click.echo(json.dumps({
                "dry_run": dry_run,
                "cleaned": [str(p) for p in cleaned],
            }))
        elif cleaned:
            action = "Would clean" if dry_run else "Cleaned"
            console.print(f"[green]{action}:[/green]")
            for path in cleaned:
                console.print(f"  {path}")
        else:
            console.print("[dim]No orphaned workspaces found[/dim]")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


# =============================================================================
# Template Commands
# =============================================================================


@cli.group()
def template() -> None:
    """Template management commands."""


@template.command("list")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.pass_context
def template_list(
    ctx: click.Context,
    output_json: bool,
) -> None:
    """List available workflow templates."""
    from .templates import get_available_templates

    templates = get_available_templates()

    if output_json:
        click.echo(json.dumps(templates, indent=2))
        return

    if not templates:
        console.print("[dim]No templates found[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("NAME", style="cyan")
    table.add_column("DESCRIPTION")

    for name, desc in templates.items():
        table.add_row(name, desc)

    console.print(table)


@template.command("show")
@click.argument("name")
@click.pass_context
def template_show(ctx: click.Context, name: str) -> None:
    """Show template source code."""
    from .templates import get_template_source

    try:
        source = get_template_source(name)
        console.print(Panel(source, title=f"Template: {name}", expand=False))
    except KeyError:
        console.print(f"[red]Template not found:[/red] {name}")
        sys.exit(1)


if __name__ == "__main__":
    cli()
