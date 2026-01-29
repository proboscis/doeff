"""
Tests for doeff-agentic TUI module.
"""



class TestTUIImports:
    """Test that TUI module imports correctly."""

    def test_import_app(self):
        """Test that AgenticTUI can be imported."""
        from doeff_agentic.tui import AgenticTUI
        assert AgenticTUI is not None

    def test_import_main(self):
        """Test that main function can be imported."""
        from doeff_agentic.tui import main
        assert callable(main)

    def test_import_screens(self):
        """Test that screens can be imported."""
        from doeff_agentic.tui.screens import WatchScreen, WorkflowListScreen
        assert WorkflowListScreen is not None
        assert WatchScreen is not None

    def test_import_widgets(self):
        """Test that widgets can be imported."""
        from doeff_agentic.tui.widgets import (
            AgentOutputPane,
            WorkflowInfoPane,
            WorkflowListItem,
        )
        assert WorkflowListItem is not None
        assert WorkflowInfoPane is not None
        assert AgentOutputPane is not None


class TestFormatRelativeTime:
    """Test relative time formatting."""

    def test_format_seconds(self):
        """Test formatting seconds."""
        from datetime import datetime, timedelta, timezone

        from doeff_agentic.tui.widgets import _format_relative_time

        now = datetime.now(timezone.utc)
        dt = now - timedelta(seconds=30)
        result = _format_relative_time(dt)
        assert result == "30s"

    def test_format_minutes(self):
        """Test formatting minutes."""
        from datetime import datetime, timedelta, timezone

        from doeff_agentic.tui.widgets import _format_relative_time

        now = datetime.now(timezone.utc)
        dt = now - timedelta(minutes=5)
        result = _format_relative_time(dt)
        assert result == "5m"

    def test_format_hours(self):
        """Test formatting hours."""
        from datetime import datetime, timedelta, timezone

        from doeff_agentic.tui.widgets import _format_relative_time

        now = datetime.now(timezone.utc)
        dt = now - timedelta(hours=2)
        result = _format_relative_time(dt)
        assert result == "2h"

    def test_format_days(self):
        """Test formatting days."""
        from datetime import datetime, timedelta, timezone

        from doeff_agentic.tui.widgets import _format_relative_time

        now = datetime.now(timezone.utc)
        dt = now - timedelta(days=3)
        result = _format_relative_time(dt)
        assert result == "3d"


class TestStatusHelpers:
    """Test status helper functions."""

    def test_get_status_symbol_workflow(self):
        """Test getting status symbols for workflow status."""
        from doeff_agentic.tui.widgets import _get_status_symbol
        from doeff_agentic.types import WorkflowStatus

        assert _get_status_symbol(WorkflowStatus.RUNNING) == "●"
        assert _get_status_symbol(WorkflowStatus.BLOCKED) == "◉"
        assert _get_status_symbol(WorkflowStatus.COMPLETED) == "✓"
        assert _get_status_symbol(WorkflowStatus.FAILED) == "✗"

    def test_get_status_symbol_agent(self):
        """Test getting status symbols for agent status."""
        from doeff_agentic.tui.widgets import _get_status_symbol
        from doeff_agentic.types import AgentStatus

        assert _get_status_symbol(AgentStatus.RUNNING) == "●"
        assert _get_status_symbol(AgentStatus.BLOCKED) == "◉"
        assert _get_status_symbol(AgentStatus.DONE) == "✓"
        assert _get_status_symbol(AgentStatus.BOOTING) == "◐"

    def test_get_status_class(self):
        """Test getting CSS class for status."""
        from doeff_agentic.tui.widgets import _get_status_class
        from doeff_agentic.types import WorkflowStatus

        assert _get_status_class(WorkflowStatus.RUNNING) == "status-running"
        assert _get_status_class(WorkflowStatus.BLOCKED) == "status-blocked"
