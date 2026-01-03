"""
Screen definitions for doeff-agentic TUI.

Contains:
- WorkflowListScreen: Interactive ps view for listing workflows
- WatchScreen: Detailed view for watching a specific workflow
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.events import Key
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Footer, Header, Input, Static

from ..types import WorkflowInfo
from .widgets import AgentOutputPane, WorkflowInfoPane, WorkflowListItem

if TYPE_CHECKING:
    from .app import AgenticTUI


class WorkflowListScreen(Screen[None]):
    """Interactive workflow list screen (ps view).

    Displays all workflows with their status and allows navigation
    and actions on selected workflow.
    """

    BINDINGS = [
        Binding("enter", "watch", "Watch", show=True),
        Binding("a", "attach", "Attach", show=True),
        Binding("s", "send", "Send", show=True),
        Binding("k", "kill", "Kill", show=True),
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

    .status-pending {
        color: $text-muted;
    }

    .status-running {
        color: $success;
    }

    .status-blocked {
        color: $warning;
    }

    .status-completed {
        color: $success;
    }

    .status-failed {
        color: $error;
    }

    .status-stopped {
        color: $text-muted;
    }

    .status-booting {
        color: $primary;
    }

    .status-done {
        color: $success;
    }

    .status-exited {
        color: $text-muted;
    }

    #footer-bar {
        dock: bottom;
        height: 1;
        background: $surface-darken-1;
        padding: 0 1;
    }

    .empty-message {
        text-align: center;
        padding: 2;
        color: $text-muted;
    }

    #send-modal {
        display: none;
        layer: modal;
        align: center middle;
    }

    #send-modal.visible {
        display: block;
    }

    #send-modal-content {
        width: 60%;
        max-width: 80;
        height: auto;
        padding: 1 2;
        border: round $primary;
        background: $surface;
    }

    #send-input {
        width: 100%;
        margin-top: 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.workflows: list[WorkflowInfo] = []
        self.selected_index = 0
        self._refresh_timer: Timer | None = None

    @property
    def tui_app(self) -> "AgenticTUI":
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
        yield Container(
            Container(
                Static("Send message to workflow:", id="send-modal-title"),
                Input(placeholder="Enter message...", id="send-input"),
                id="send-modal-content",
            ),
            id="send-modal",
        )
        yield Footer()

    def on_mount(self) -> None:
        """Called when the screen is mounted."""
        self.refresh_workflows()
        # Set up auto-refresh every 2 seconds
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
            # Post UI updates back to main thread
            self.app.call_from_thread(self._apply_workflows, workflows)
        except Exception as e:
            self.app.call_from_thread(self.notify, f"Error refreshing: {e}", severity="error")

    def _apply_workflows(self, workflows: list[WorkflowInfo]) -> None:
        """Apply workflow data to UI (main thread only)."""
        self.workflows = workflows
        # Ensure selected_index is within bounds
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
            self.app.push_screen(WatchScreen(workflow.id))

    def action_attach(self) -> None:
        """Attach to the selected workflow's agent tmux session."""
        workflow = self._get_selected_workflow()
        if workflow:
            self._attach_to_workflow(workflow)

    def _attach_to_workflow(self, workflow: WorkflowInfo) -> None:
        """Attach to workflow's tmux session."""
        # Suspend the app and exec tmux attach
        self.app.suspend()
        try:
            self.tui_app.api.attach(workflow.id)
        except Exception as e:
            self.notify(f"Failed to attach: {e}", severity="error")
        finally:
            self.app.resume()

    def action_send(self) -> None:
        """Show send message modal."""
        workflow = self._get_selected_workflow()
        if workflow:
            modal = self.query_one("#send-modal")
            modal.add_class("visible")
            input_widget = self.query_one("#send-input", Input)
            input_widget.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle message submission."""
        if event.input.id == "send-input":
            message = event.value
            workflow = self._get_selected_workflow()
            if workflow and message:
                try:
                    success = self.tui_app.api.send_message(workflow.id, message)
                    if success:
                        self.notify("Message sent", severity="information")
                    else:
                        self.notify("Failed to send message", severity="error")
                except Exception as e:
                    self.notify(f"Error: {e}", severity="error")
            event.input.value = ""
            modal = self.query_one("#send-modal")
            modal.remove_class("visible")

    def on_key(self, event: Key) -> None:
        """Handle key events."""
        if event.key == "escape":
            modal = self.query_one("#send-modal")
            if "visible" in modal.classes:
                modal.remove_class("visible")
                event.stop()

    def action_kill(self) -> None:
        """Kill the selected workflow."""
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


class WatchScreen(Screen[None]):
    """Detailed watch view for a specific workflow.

    Shows workflow info pane and agent output pane with real-time updates.
    """

    BINDINGS = [
        Binding("a", "attach", "Attach", show=True),
        Binding("s", "send", "Send", show=True),
        Binding("k", "kill", "Kill", show=True),
        Binding("q,escape", "back", "Back", show=True),
        Binding("tab", "switch_pane", "Switch Pane", show=True),
        Binding("up,k", "scroll_up", "Up", show=False),
        Binding("down,j", "scroll_down", "Down", show=False),
    ]

    CSS = """
    WatchScreen {
        layout: vertical;
    }

    #watch-header {
        height: 1;
        background: $primary;
        color: $text;
        padding: 0 1;
    }

    #panes-container {
        height: 1fr;
        layout: horizontal;
    }

    #workflow-pane {
        width: 1fr;
        border: round $primary;
        padding: 0 1;
    }

    #workflow-pane.focused {
        border: round $success;
    }

    #agent-pane {
        width: 2fr;
        border: round $secondary;
        padding: 0 1;
    }

    #agent-pane.focused {
        border: round $success;
    }

    #workflow-pane-title, #agent-pane-title {
        text-style: bold;
        padding: 0;
    }

    #workflow-info-content {
        height: 1fr;
    }

    #agent-output-content {
        height: 1fr;
    }

    .info-row {
        height: 1;
    }

    .info-label {
        width: 12;
        color: $text-muted;
    }

    .info-value {
        width: 1fr;
    }

    #send-modal {
        display: none;
        layer: modal;
        align: center middle;
    }

    #send-modal.visible {
        display: block;
    }

    #send-modal-content {
        width: 60%;
        max-width: 80;
        height: auto;
        padding: 1 2;
        border: round $primary;
        background: $surface;
    }

    #send-input {
        width: 100%;
        margin-top: 1;
    }
    """

    def __init__(self, workflow_id: str) -> None:
        super().__init__()
        self.workflow_id = workflow_id
        self.workflow: WorkflowInfo | None = None
        self.focused_pane = "workflow"  # or "agent"
        self._refresh_timer: Timer | None = None

    @property
    def tui_app(self) -> "AgenticTUI":
        """Get the typed app instance."""
        from .app import AgenticTUI
        assert isinstance(self.app, AgenticTUI)
        return self.app

    def compose(self) -> ComposeResult:
        """Compose the screen layout."""
        yield Header()
        yield Static(
            f"  doeff-agentic watch {self.workflow_id}",
            id="watch-header",
        )
        yield Horizontal(
            Container(
                Static("Workflow", id="workflow-pane-title"),
                VerticalScroll(
                    WorkflowInfoPane(id="workflow-info-content"),
                ),
                id="workflow-pane",
                classes="focused",
            ),
            Container(
                Static("Agent Output", id="agent-pane-title"),
                VerticalScroll(
                    AgentOutputPane(id="agent-output-content"),
                ),
                id="agent-pane",
            ),
            id="panes-container",
        )
        yield Container(
            Container(
                Static("Send message to agent:", id="send-modal-title"),
                Input(placeholder="Enter message...", id="send-input"),
                id="send-modal-content",
            ),
            id="send-modal",
        )
        yield Footer()

    def on_mount(self) -> None:
        """Called when the screen is mounted."""
        self.refresh_workflow()
        # Set up auto-refresh every second
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
                # Workflow was deleted or not found - go back to list
                self.app.call_from_thread(self.app.pop_screen)
                return

            agent_output = self.tui_app.api.get_agent_output(self.workflow_id, lines=50)
            # Post UI updates back to main thread
            self.app.call_from_thread(self._apply_workflow_data, workflow, agent_output)
        except Exception as e:
            self.app.call_from_thread(self.notify, f"Error: {e}", severity="error")

    def _apply_workflow_data(self, workflow: WorkflowInfo, agent_output: str) -> None:
        """Apply workflow data to UI (main thread only)."""
        self.workflow = workflow

        # Update workflow info pane
        info_pane = self.query_one("#workflow-info-content", WorkflowInfoPane)
        info_pane.update_workflow(workflow)

        # Update agent output pane
        output_pane = self.query_one("#agent-output-content", AgentOutputPane)
        output_pane.update_output(agent_output, workflow)

    def action_switch_pane(self) -> None:
        """Switch focus between panes."""
        workflow_pane = self.query_one("#workflow-pane", Container)
        agent_pane = self.query_one("#agent-pane", Container)

        if self.focused_pane == "workflow":
            self.focused_pane = "agent"
            workflow_pane.remove_class("focused")
            agent_pane.add_class("focused")
        else:
            self.focused_pane = "workflow"
            agent_pane.remove_class("focused")
            workflow_pane.add_class("focused")

    def action_scroll_up(self) -> None:
        """Scroll up in the focused pane."""
        if self.focused_pane == "workflow":
            pane = self.query_one("#workflow-pane VerticalScroll")
        else:
            pane = self.query_one("#agent-pane VerticalScroll")
        pane.scroll_up()

    def action_scroll_down(self) -> None:
        """Scroll down in the focused pane."""
        if self.focused_pane == "workflow":
            pane = self.query_one("#workflow-pane VerticalScroll")
        else:
            pane = self.query_one("#agent-pane VerticalScroll")
        pane.scroll_down()

    def action_attach(self) -> None:
        """Attach to the workflow's agent tmux session."""
        if self.workflow:
            self.app.suspend()
            try:
                self.tui_app.api.attach(self.workflow.id)
            except Exception as e:
                self.notify(f"Failed to attach: {e}", severity="error")
            finally:
                self.app.resume()

    def action_send(self) -> None:
        """Show send message modal."""
        if self.workflow:
            modal = self.query_one("#send-modal")
            modal.add_class("visible")
            input_widget = self.query_one("#send-input", Input)
            input_widget.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle message submission."""
        if event.input.id == "send-input":
            message = event.value
            if self.workflow and message:
                try:
                    success = self.tui_app.api.send_message(self.workflow.id, message)
                    if success:
                        self.notify("Message sent", severity="information")
                    else:
                        self.notify("Failed to send message", severity="error")
                except Exception as e:
                    self.notify(f"Error: {e}", severity="error")
            event.input.value = ""
            modal = self.query_one("#send-modal")
            modal.remove_class("visible")

    def on_key(self, event: Key) -> None:
        """Handle key events."""
        if event.key == "escape":
            modal = self.query_one("#send-modal")
            if "visible" in modal.classes:
                modal.remove_class("visible")
                event.stop()
                return

    def action_kill(self) -> None:
        """Kill the workflow."""
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
