"""
Custom widgets for doeff-agentic TUI.

Contains:
- WorkflowListItem: A single row in the workflow list
- WorkflowInfoPane: Detailed workflow information panel
- AgentOutputPane: Agent output display panel
"""

from __future__ import annotations

from datetime import datetime, timezone

from textual.app import ComposeResult
from textual.widgets import Static

from ..types import AgentStatus, WorkflowInfo, WorkflowStatus


def _format_relative_time(dt: datetime) -> str:
    """Format a datetime as relative time (e.g., '5m ago', '2h ago')."""
    now = datetime.now(timezone.utc)
    # Ensure dt is timezone-aware
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
        WorkflowStatus.PENDING: "○",
        WorkflowStatus.RUNNING: "●",
        WorkflowStatus.BLOCKED: "◉",
        WorkflowStatus.COMPLETED: "✓",
        WorkflowStatus.FAILED: "✗",
        WorkflowStatus.STOPPED: "○",
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
      ● a3f8b2c  pr-review-main       review-agent  [blocked]  2m
    """

    def __init__(self, workflow: WorkflowInfo, is_selected: bool = False) -> None:
        super().__init__()
        self.workflow = workflow
        self.is_selected = is_selected

    def compose(self) -> ComposeResult:
        """Compose the widget."""
        wf = self.workflow
        symbol = _get_status_symbol(wf.status)
        status_text = wf.status.value
        agent = wf.current_agent or "-"
        time_str = _format_relative_time(wf.updated_at)

        # Format: symbol  id       name                agent        [status]  time
        content = (
            f"  {symbol}  {wf.id}  {wf.name:<20}  {agent:<14}  "
            f"[{status_text:<9}]  {time_str:>4}"
        )
        yield Static(content)

    def on_mount(self) -> None:
        """Apply styles on mount."""
        self.add_class("workflow-item")
        self.add_class(_get_status_class(self.workflow.status))
        if self.is_selected:
            self.add_class("selected")


class WorkflowInfoPane(Static):
    """Displays detailed workflow information in the watch view.

    Shows:
    - Current status and progress
    - Workflow trace/stack
    - All agents with their status
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

        # Status header
        symbol = _get_status_symbol(wf.status)
        status_desc = self._get_status_description(wf)
        lines.append(f"[{wf.status.value}] {status_desc}")
        lines.append("")

        # Workflow info
        lines.append(f"ID:      {wf.id}")
        lines.append(f"Name:    {wf.name}")
        lines.append(f"Status:  {symbol} {wf.status.value}")
        lines.append(f"Started: {_format_relative_time(wf.started_at)} ago")
        lines.append(f"Updated: {_format_relative_time(wf.updated_at)} ago")
        lines.append("")

        # Agent tree/list
        if wf.agents:
            lines.append("Agents:")
            for agent in wf.agents:
                is_current = agent.name == wf.current_agent
                symbol = _get_status_symbol(agent.status)
                marker = "←" if is_current else " "
                lines.append(
                    f"  {symbol} {agent.name:<16} [{agent.status.value}] {marker}"
                )

            # Current agent details
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

        # Last slog if available
        if wf.last_slog:
            lines.append("")
            lines.append("Last Log:")
            for key, value in wf.last_slog.items():
                lines.append(f"  {key}: {value}")

        # Error if any
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

    Shows the captured output from the agent's tmux pane.
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
                # Find the agent
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
