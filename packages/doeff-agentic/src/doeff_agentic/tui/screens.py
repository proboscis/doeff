"""
Screen definitions for doeff-agentic TUI.

Contains:
- WorkflowListScreen: Interactive ps view for listing workflows
- WorkflowWatchScreen: Workflow-level view showing all agents
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical, VerticalScroll
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Footer, Header, Static

from ..types import WorkflowInfo
from .widgets import (
    AgentListItem,
    CurrentActivityPane,
    WorkflowHeaderPane,
    WorkflowListItem,
)

if TYPE_CHECKING:
    from .app import AgenticTUI


class WorkflowListScreen(Screen[None]):
    """Interactive workflow list screen (ps view).

    Displays all workflows with their status and allows navigation
    and actions on selected workflow.
    """

    BINDINGS = [
        Binding("enter", "watch", "Watch", show=True),
        Binding("s", "stop", "Stop", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("q", "quit", "Quit", show=True),
        Binding("up,k", "cursor_up", "Up", show=False),
        Binding("down,j", "cursor_down", "Down", show=False),
    ]

    CSS = """
    WorkflowListScreen {
        layout: vertical;
    }

    #workflow-list-container {
        border: round $primary;
        height: 1fr;
        padding: 0 1;
    }

    #workflow-list-title {
        text-style: bold;
        color: $primary;
        padding: 0 1;
    }

    #workflow-list {
        height: 1fr;
    }

    .workflow-item {
        height: 1;
        padding: 0 1;
    }

    .workflow-item.selected {
        background: $primary 30%;
    }

    .workflow-item:hover {
        background: $primary 20%;
    }

    .status-pending { color: $text-muted; }
    .status-running { color: $success; }
    .status-blocked { color: $warning; }
    .status-completed, .status-done { color: $success; }
    .status-failed, .status-error { color: $error; }
    .status-stopped, .status-aborted { color: $text-muted; }
    .status-booting { color: $primary; }
    .status-exited { color: $text-muted; }

    .empty-message {
        text-align: center;
        padding: 2;
        color: $text-muted;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.workflows: list[WorkflowInfo] = []
        self.selected_index = 0
        self._refresh_timer: Timer | None = None

    @property
    def tui_app(self) -> AgenticTUI:
        """Get the typed app instance."""
        from .app import AgenticTUI

        assert isinstance(self.app, AgenticTUI)
        return self.app

    def compose(self) -> ComposeResult:
        """Compose the screen layout."""
        yield Header()
        yield Container(
            Static("Active Workflows", id="workflow-list-title"),
            VerticalScroll(id="workflow-list"),
            id="workflow-list-container",
        )
        yield Footer()

    def on_mount(self) -> None:
        """Called when the screen is mounted."""
        self.refresh_workflows()
        self._refresh_timer = self.set_interval(2.0, self.refresh_workflows)

    def on_unmount(self) -> None:
        """Called when the screen is unmounted."""
        if self._refresh_timer is not None:
            self._refresh_timer.stop()

    @work(exclusive=True, thread=True)
    def refresh_workflows(self) -> None:
        """Refresh the workflow list from state files."""
        try:
            workflows = self.tui_app.api.list_workflows()
            self.app.call_from_thread(self._apply_workflows, workflows)
        except Exception as e:
            self.app.call_from_thread(
                self.notify, f"Error refreshing: {e}", severity="error"
            )

    def _apply_workflows(self, workflows: list[WorkflowInfo]) -> None:
        """Apply workflow data to UI (main thread only)."""
        self.workflows = workflows
        if self.workflows:
            self.selected_index = min(self.selected_index, len(self.workflows) - 1)
        else:
            self.selected_index = 0
        self._update_list_display()

    def _update_list_display(self) -> None:
        """Update the list display with current workflows."""
        list_container = self.query_one("#workflow-list", VerticalScroll)
        list_container.remove_children()

        if not self.workflows:
            list_container.mount(
                Static("No active workflows", classes="empty-message")
            )
            return

        for i, workflow in enumerate(self.workflows):
            is_selected = i == self.selected_index
            item = WorkflowListItem(workflow, is_selected)
            list_container.mount(item)

    def _get_selected_workflow(self) -> WorkflowInfo | None:
        """Get the currently selected workflow."""
        if 0 <= self.selected_index < len(self.workflows):
            return self.workflows[self.selected_index]
        return None

    def action_cursor_up(self) -> None:
        """Move selection up."""
        if self.selected_index > 0:
            self.selected_index -= 1
            self._update_list_display()

    def action_cursor_down(self) -> None:
        """Move selection down."""
        if self.selected_index < len(self.workflows) - 1:
            self.selected_index += 1
            self._update_list_display()

    def action_watch(self) -> None:
        """Watch the selected workflow."""
        workflow = self._get_selected_workflow()
        if workflow:
            self.app.push_screen(WorkflowWatchScreen(workflow.id))

    def action_stop(self) -> None:
        """Stop the selected workflow."""
        workflow = self._get_selected_workflow()
        if workflow:
            try:
                stopped = self.tui_app.api.stop(workflow.id)
                if stopped:
                    self.notify(f"Stopped: {', '.join(stopped)}", severity="information")
                else:
                    self.notify("No agents to stop", severity="warning")
            except Exception as e:
                self.notify(f"Error: {e}", severity="error")
            self.refresh_workflows()

    def action_refresh(self) -> None:
        """Manually refresh the workflow list."""
        self.refresh_workflows()


class WorkflowWatchScreen(Screen[None]):
    """Workflow-level watch view showing all agents.

    Design per spec:
    ┌─ Workflow a3f8b2c: PR Review ────────────────────────────────────┐
    │ Status: running                                                  │
    │ Started: 5m ago                                                  │
    ├──────────────────────────────────────────────────────────────────┤
    │ Agents:                                                          │
    │ > ● reviewer    [done]     env-abc  "Found 3 issues"            │
    │   ◐ fixer       [running]  env-abc  "Fixing issue 2/3..."       │
    │   ○ tester      [pending]  env-def  -                           │
    ├──────────────────────────────────────────────────────────────────┤
    │ Current Activity (fixer):                                        │
    │ > Applying fix for unused import...                              │
    │ > Removing line 42: import os                                    │
    │ > Running tests to verify fix...                                 │
    ├──────────────────────────────────────────────────────────────────┤
    │ [a]ttach  [l]ogs  [q]uit  [↑↓] select agent                     │
    └──────────────────────────────────────────────────────────────────┘
    """

    BINDINGS = [
        Binding("a", "attach", "Attach", show=True),
        Binding("l", "logs", "Logs", show=True),
        Binding("s", "stop", "Stop", show=True),
        Binding("q,escape", "back", "Back", show=True),
        Binding("up,k", "cursor_up", "Up", show=False),
        Binding("down,j", "cursor_down", "Down", show=False),
    ]

    CSS = """
    WorkflowWatchScreen {
        layout: vertical;
    }

    #workflow-header {
        height: auto;
        min-height: 4;
        border: round $primary;
        padding: 0 1;
        margin-bottom: 1;
    }

    #agents-container {
        height: 1fr;
        min-height: 5;
        border: round $secondary;
        padding: 0 1;
        margin-bottom: 1;
    }

    #agents-title {
        text-style: bold;
        color: $secondary;
    }

    #agents-list {
        height: 1fr;
    }

    .agent-item {
        height: 1;
        padding: 0 1;
    }

    .agent-item.selected {
        background: $secondary 30%;
    }

    .agent-item:hover {
        background: $secondary 20%;
    }

    #activity-container {
        height: 1fr;
        border: round $accent;
        padding: 0 1;
    }

    #activity-title {
        text-style: bold;
        color: $accent;
    }

    #activity-content {
        height: 1fr;
    }

    .status-pending { color: $text-muted; }
    .status-running { color: $success; }
    .status-blocked { color: $warning; }
    .status-completed, .status-done { color: $success; }
    .status-failed, .status-error { color: $error; }
    .status-stopped, .status-aborted { color: $text-muted; }
    .status-booting { color: $primary; }
    .status-exited { color: $text-muted; }

    .empty-message {
        text-align: center;
        padding: 1;
        color: $text-muted;
    }
    """

    def __init__(self, workflow_id: str) -> None:
        super().__init__()
        self.workflow_id = workflow_id
        self.workflow: WorkflowInfo | None = None
        self.selected_agent_index = 0
        self.agent_outputs: dict[str, str] = {}  # agent_name -> output snippet
        self._refresh_timer: Timer | None = None

    @property
    def tui_app(self) -> AgenticTUI:
        """Get the typed app instance."""
        from .app import AgenticTUI

        assert isinstance(self.app, AgenticTUI)
        return self.app

    def compose(self) -> ComposeResult:
        """Compose the screen layout."""
        yield Header()
        yield Vertical(
            WorkflowHeaderPane(id="workflow-header"),
            Container(
                Static("Agents:", id="agents-title"),
                VerticalScroll(id="agents-list"),
                id="agents-container",
            ),
            Container(
                Static("Current Activity:", id="activity-title"),
                VerticalScroll(
                    CurrentActivityPane(id="activity-content"),
                ),
                id="activity-container",
            ),
        )
        yield Footer()

    def on_mount(self) -> None:
        """Called when the screen is mounted."""
        self.refresh_workflow()
        self._refresh_timer = self.set_interval(1.0, self.refresh_workflow)

    def on_unmount(self) -> None:
        """Called when the screen is unmounted."""
        if self._refresh_timer is not None:
            self._refresh_timer.stop()

    @work(exclusive=True, thread=True)
    def refresh_workflow(self) -> None:
        """Refresh workflow and agent data."""
        try:
            workflow = self.tui_app.api.get_workflow(self.workflow_id)
            if workflow is None:
                self.app.call_from_thread(self.app.pop_screen)
                return

            # Get output snippets for each agent
            agent_outputs: dict[str, str] = {}
            for agent in workflow.agents:
                try:
                    output = self.tui_app.api.get_agent_output(
                        self.workflow_id, agent.name, lines=5
                    )
                    # Get last non-empty line as snippet
                    lines = [l for l in output.strip().split("\n") if l.strip()]
                    agent_outputs[agent.name] = lines[-1][:50] if lines else "-"
                except Exception:
                    agent_outputs[agent.name] = "-"

            # Get detailed output for selected agent
            selected_output = ""
            if workflow.agents and self.selected_agent_index < len(workflow.agents):
                selected_agent = workflow.agents[self.selected_agent_index]
                try:
                    selected_output = self.tui_app.api.get_agent_output(
                        self.workflow_id, selected_agent.name, lines=20
                    )
                except Exception:
                    selected_output = "(No output)"

            self.app.call_from_thread(
                self._apply_workflow_data, workflow, agent_outputs, selected_output
            )
        except Exception as e:
            self.app.call_from_thread(self.notify, f"Error: {e}", severity="error")

    def _apply_workflow_data(
        self,
        workflow: WorkflowInfo,
        agent_outputs: dict[str, str],
        selected_output: str,
    ) -> None:
        """Apply workflow data to UI (main thread only)."""
        self.workflow = workflow
        self.agent_outputs = agent_outputs

        # Ensure selected index is valid
        if workflow.agents:
            self.selected_agent_index = min(
                self.selected_agent_index, len(workflow.agents) - 1
            )
        else:
            self.selected_agent_index = 0

        # Update header
        header_pane = self.query_one("#workflow-header", WorkflowHeaderPane)
        header_pane.update_workflow(workflow)

        # Update agents list
        self._update_agents_list()

        # Update activity pane
        activity_title = self.query_one("#activity-title", Static)
        activity_pane = self.query_one("#activity-content", CurrentActivityPane)

        if workflow.agents and self.selected_agent_index < len(workflow.agents):
            selected_agent = workflow.agents[self.selected_agent_index]
            activity_title.update(f"Current Activity ({selected_agent.name}):")
            activity_pane.update_output(selected_output)
        else:
            activity_title.update("Current Activity:")
            activity_pane.update_output("(No agent selected)")

    def _update_agents_list(self) -> None:
        """Update the agents list display."""
        list_container = self.query_one("#agents-list", VerticalScroll)
        list_container.remove_children()

        if not self.workflow or not self.workflow.agents:
            list_container.mount(Static("No agents", classes="empty-message"))
            return

        for i, agent in enumerate(self.workflow.agents):
            is_selected = i == self.selected_agent_index
            snippet = self.agent_outputs.get(agent.name, "-")
            item = AgentListItem(agent, snippet, is_selected)
            list_container.mount(item)

    def _get_selected_agent_name(self) -> str | None:
        """Get the currently selected agent name."""
        if (
            self.workflow
            and self.workflow.agents
            and 0 <= self.selected_agent_index < len(self.workflow.agents)
        ):
            return self.workflow.agents[self.selected_agent_index].name
        return None

    def action_cursor_up(self) -> None:
        """Move agent selection up."""
        if self.selected_agent_index > 0:
            self.selected_agent_index -= 1
            self._update_agents_list()
            # Trigger refresh to update activity pane
            self.refresh_workflow()

    def action_cursor_down(self) -> None:
        """Move agent selection down."""
        if self.workflow and self.selected_agent_index < len(self.workflow.agents) - 1:
            self.selected_agent_index += 1
            self._update_agents_list()
            # Trigger refresh to update activity pane
            self.refresh_workflow()

    def action_attach(self) -> None:
        """Attach to the selected agent's tmux session."""
        agent_name = self._get_selected_agent_name()
        if self.workflow and agent_name:
            self.app.suspend()
            try:
                self.tui_app.api.attach(self.workflow.id, agent_name)
            except Exception as e:
                self.notify(f"Failed to attach: {e}", severity="error")
            finally:
                self.app.resume()  # type: ignore[attr-defined]

    def action_logs(self) -> None:
        """View logs for the selected agent."""
        agent_name = self._get_selected_agent_name()
        if self.workflow and agent_name:
            # Show logs in a full-screen view
            self.app.push_screen(AgentLogsScreen(self.workflow.id, agent_name))

    def action_stop(self) -> None:
        """Stop the workflow."""
        if self.workflow:
            try:
                stopped = self.tui_app.api.stop(self.workflow.id)
                if stopped:
                    self.notify(f"Stopped: {', '.join(stopped)}", severity="information")
            except Exception as e:
                self.notify(f"Error: {e}", severity="error")
            self.app.pop_screen()

    def action_back(self) -> None:
        """Go back to workflow list."""
        self.app.pop_screen()


class AgentLogsScreen(Screen[None]):
    """Full-screen view of agent logs."""

    BINDINGS = [
        Binding("q,escape", "back", "Back", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("up,k", "scroll_up", "Up", show=False),
        Binding("down,j", "scroll_down", "Down", show=False),
    ]

    CSS = """
    AgentLogsScreen {
        layout: vertical;
    }

    #logs-header {
        height: 1;
        background: $primary;
        color: $text;
        padding: 0 1;
    }

    #logs-container {
        height: 1fr;
        border: round $secondary;
        padding: 0 1;
    }

    #logs-content {
        height: 1fr;
    }
    """

    def __init__(self, workflow_id: str, agent_name: str) -> None:
        super().__init__()
        self.workflow_id = workflow_id
        self.agent_name = agent_name
        self.output = ""
        self._refresh_timer: Timer | None = None

    @property
    def tui_app(self) -> AgenticTUI:
        """Get the typed app instance."""
        from .app import AgenticTUI

        assert isinstance(self.app, AgenticTUI)
        return self.app

    def compose(self) -> ComposeResult:
        """Compose the screen layout."""
        yield Header()
        yield Static(
            f"  Logs: {self.workflow_id}:{self.agent_name}",
            id="logs-header",
        )
        yield Container(
            VerticalScroll(
                Static("Loading...", id="logs-content"),
            ),
            id="logs-container",
        )
        yield Footer()

    def on_mount(self) -> None:
        """Called when the screen is mounted."""
        self.refresh_logs()
        self._refresh_timer = self.set_interval(1.0, self.refresh_logs)

    def on_unmount(self) -> None:
        """Called when the screen is unmounted."""
        if self._refresh_timer is not None:
            self._refresh_timer.stop()

    @work(exclusive=True, thread=True)
    def refresh_logs(self) -> None:
        """Refresh log content."""
        try:
            output = self.tui_app.api.get_agent_output(
                self.workflow_id, self.agent_name, lines=200
            )
            self.app.call_from_thread(self._apply_logs, output)
        except Exception as e:
            self.app.call_from_thread(self._apply_logs, f"Error: {e}")

    def _apply_logs(self, output: str) -> None:
        """Apply log content to UI."""
        self.output = output
        logs_content = self.query_one("#logs-content", Static)
        logs_content.update(output or "(No output)")

    def action_scroll_up(self) -> None:
        """Scroll up."""
        container = self.query_one("#logs-container VerticalScroll")
        container.scroll_up()

    def action_scroll_down(self) -> None:
        """Scroll down."""
        container = self.query_one("#logs-container VerticalScroll")
        container.scroll_down()

    def action_refresh(self) -> None:
        """Manually refresh logs."""
        self.refresh_logs()

    def action_back(self) -> None:
        """Go back to watch screen."""
        self.app.pop_screen()


# Backward compatibility alias
WatchScreen = WorkflowWatchScreen
