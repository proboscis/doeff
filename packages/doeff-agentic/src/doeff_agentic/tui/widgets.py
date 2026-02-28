"""
Custom widgets for doeff-agentic TUI.

Contains:
- WorkflowListItem: A single row in the workflow list
- WorkflowHeaderPane: Workflow info header
- AgentListItem: A single agent row with status and snippet
- CurrentActivityPane: Selected agent's output
"""


from datetime import datetime, timezone

from textual.app import ComposeResult
from textual.widgets import Static

from ..types import AgentInfo, AgentStatus, WorkflowInfo, WorkflowStatus


def _format_relative_time(dt: datetime) -> str:
    """Format a datetime as relative time (e.g., '5m ago', '2h ago')."""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    diff = now - dt
    total_seconds = int(diff.total_seconds())

    if total_seconds < 0:
        return "now"
    if total_seconds < 60:
        return f"{total_seconds}s"
    if total_seconds < 3600:
        minutes = total_seconds // 60
        return f"{minutes}m"
    if total_seconds < 86400:
        hours = total_seconds // 3600
        return f"{hours}h"
    days = total_seconds // 86400
    return f"{days}d"


def _get_status_symbol(status: WorkflowStatus | AgentStatus) -> str:
    """Get the status indicator symbol."""
    status_symbols = {
        # Workflow statuses
        WorkflowStatus.PENDING: "○",
        WorkflowStatus.RUNNING: "●",
        WorkflowStatus.BLOCKED: "◉",
        WorkflowStatus.COMPLETED: "✓",
        WorkflowStatus.FAILED: "✗",
        WorkflowStatus.STOPPED: "○",
        # Agent statuses
        AgentStatus.PENDING: "○",
        AgentStatus.BOOTING: "◐",
        AgentStatus.RUNNING: "●",
        AgentStatus.BLOCKED: "◉",
        AgentStatus.DONE: "✓",
        AgentStatus.FAILED: "✗",
        AgentStatus.EXITED: "○",
        AgentStatus.STOPPED: "○",
    }
    return status_symbols.get(status, "?")


def _get_status_class(status: WorkflowStatus | AgentStatus) -> str:
    """Get the CSS class for a status."""
    return f"status-{status.value}"


class WorkflowListItem(Static):
    """A single workflow item in the list view.

    Display format:
      ● a3f8b2c  pr-review      reviewer(done), fixer(running)  2m
    """

    def __init__(self, workflow: WorkflowInfo, is_selected: bool = False) -> None:
        super().__init__()
        self.workflow = workflow
        self.is_selected = is_selected

    def compose(self) -> ComposeResult:
        """Compose the widget."""
        wf = self.workflow
        symbol = _get_status_symbol(wf.status)
        time_str = _format_relative_time(wf.updated_at)

        # Format agents list
        agents_str = "-"
        if wf.agents:
            agent_parts = []
            for a in wf.agents:
                agent_parts.append(f"{a.name}({a.status.value})")
            agents_str = ", ".join(agent_parts)
            # Truncate if too long
            if len(agents_str) > 40:
                agents_str = agents_str[:37] + "..."

        # Format: symbol  id       name            agents                    time
        content = (
            f"  {symbol}  {wf.id}  {wf.name:<14}  {agents_str:<40}  {time_str:>4}"
        )
        yield Static(content)

    def on_mount(self) -> None:
        """Apply styles on mount."""
        self.add_class("workflow-item")
        self.add_class(_get_status_class(self.workflow.status))
        if self.is_selected:
            self.add_class("selected")


class WorkflowHeaderPane(Static):
    """Workflow info header pane.

    Shows:
    - Workflow ID and name
    - Status
    - Started time
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.workflow: WorkflowInfo | None = None

    def update_workflow(self, workflow: WorkflowInfo) -> None:
        """Update the displayed workflow information."""
        self.workflow = workflow
        self._render_content()

    def _render_content(self) -> None:
        """Render the workflow header content."""
        if self.workflow is None:
            self.update("No workflow selected")
            return

        wf = self.workflow
        symbol = _get_status_symbol(wf.status)
        started = _format_relative_time(wf.started_at)

        lines = [
            f"Workflow {wf.id}: {wf.name or 'Unnamed'}",
            f"Status: {symbol} {wf.status.value}",
            f"Started: {started} ago",
        ]

        if wf.error:
            lines.append(f"Error: {wf.error}")

        self.update("\n".join(lines))


class AgentListItem(Static):
    """A single agent item in the agents list.

    Display format:
      > ● reviewer    [done]     env-abc  "Found 3 issues"
    """

    def __init__(
        self,
        agent: AgentInfo,
        snippet: str = "-",
        is_selected: bool = False,
    ) -> None:
        super().__init__()
        self.agent = agent
        self.snippet = snippet
        self.is_selected = is_selected

    def compose(self) -> ComposeResult:
        """Compose the widget."""
        a = self.agent
        symbol = _get_status_symbol(a.status)
        marker = ">" if self.is_selected else " "

        # Extract env from session name (doeff-<workflow>-<name>)
        env_hint = "shared"
        if a.session_name:
            parts = a.session_name.split("-")
            if len(parts) >= 2:
                env_hint = parts[1][:6]

        # Truncate snippet
        snippet = self.snippet
        if len(snippet) > 30:
            snippet = snippet[:27] + "..."

        # Format: marker symbol name       [status]   env      snippet
        content = (
            f"  {marker} {symbol} {a.name:<12}  [{a.status.value:<8}]  "
            f'{env_hint:<8}  "{snippet}"'
        )
        yield Static(content)

    def on_mount(self) -> None:
        """Apply styles on mount."""
        self.add_class("agent-item")
        self.add_class(_get_status_class(self.agent.status))
        if self.is_selected:
            self.add_class("selected")


class CurrentActivityPane(Static):
    """Displays current activity output for the selected agent."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.output: str = ""

    def update_output(self, output: str) -> None:
        """Update the displayed output."""
        self.output = output
        self._render_content()

    def _render_content(self) -> None:
        """Render the output content."""
        if self.output:
            # Show last lines with > prefix
            lines = self.output.strip().split("\n")[-15:]
            formatted = "\n".join(f"> {line}" for line in lines)
            self.update(formatted)
        else:
            self.update("(No activity)")


# Legacy widgets for backward compatibility


class WorkflowInfoPane(Static):
    """Displays detailed workflow information in the watch view.

    (Legacy - kept for backward compatibility)
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.workflow: WorkflowInfo | None = None

    def update_workflow(self, workflow: WorkflowInfo) -> None:
        """Update the displayed workflow information."""
        self.workflow = workflow
        self._render_content()

    def _render_content(self) -> None:
        """Render the workflow info content."""
        if self.workflow is None:
            self.update("No workflow selected")
            return

        wf = self.workflow
        lines: list[str] = []

        symbol = _get_status_symbol(wf.status)
        status_desc = self._get_status_description(wf)
        lines.append(f"[{wf.status.value}] {status_desc}")
        lines.append("")

        lines.append(f"ID:      {wf.id}")
        lines.append(f"Name:    {wf.name}")
        lines.append(f"Status:  {symbol} {wf.status.value}")
        lines.append(f"Started: {_format_relative_time(wf.started_at)} ago")
        lines.append(f"Updated: {_format_relative_time(wf.updated_at)} ago")
        lines.append("")

        if wf.agents:
            lines.append("Agents:")
            for agent in wf.agents:
                is_current = agent.name == wf.current_agent
                symbol = _get_status_symbol(agent.status)
                marker = "←" if is_current else " "
                lines.append(
                    f"  {symbol} {agent.name:<16} [{agent.status.value}] {marker}"
                )

            if wf.current_agent:
                lines.append("")
                current = next(
                    (a for a in wf.agents if a.name == wf.current_agent), None
                )
                if current:
                    lines.append(f"Current Agent: {current.name}")
                    lines.append(f"  Session: {current.session_name}")
                    if current.pane_id:
                        lines.append(f"  Pane: {current.pane_id}")

        if wf.last_slog:
            lines.append("")
            lines.append("Last Log:")
            for key, value in wf.last_slog.items():
                lines.append(f"  {key}: {value}")

        if wf.error:
            lines.append("")
            lines.append(f"Error: {wf.error}")

        self.update("\n".join(lines))

    def _get_status_description(self, wf: WorkflowInfo) -> str:
        """Get a human-readable status description."""
        if wf.status == WorkflowStatus.BLOCKED:
            return "Waiting for input"
        if wf.status == WorkflowStatus.RUNNING:
            if wf.current_agent:
                return f"Running {wf.current_agent}"
            return "Running..."
        if wf.status == WorkflowStatus.COMPLETED:
            return "Completed successfully"
        if wf.status == WorkflowStatus.FAILED:
            return wf.error or "Failed"
        if wf.status == WorkflowStatus.STOPPED:
            return "Stopped by user"
        return wf.status.value.title()


class AgentOutputPane(Static):
    """Displays agent output in the watch view.

    (Legacy - kept for backward compatibility)
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.output: str = ""
        self.workflow: WorkflowInfo | None = None

    def update_output(self, output: str, workflow: WorkflowInfo | None = None) -> None:
        """Update the displayed output."""
        self.output = output
        self.workflow = workflow
        self._render_content()

    def _render_content(self) -> None:
        """Render the agent output content."""
        lines: list[str] = []

        if self.workflow:
            wf = self.workflow
            if wf.current_agent:
                agent = next(
                    (a for a in wf.agents if a.name == wf.current_agent), None
                )
                if agent:
                    symbol = _get_status_symbol(agent.status)
                    lines.append(f"Agent: {agent.name}")
                    lines.append(f"Status: {symbol} {agent.status.value}")
                    lines.append(f"Session: {agent.session_name}")
                    lines.append("-" * 40)
                    lines.append("")

        if self.output:
            lines.append(self.output)
        else:
            lines.append("(No output captured)")

        self.update("\n".join(lines))
