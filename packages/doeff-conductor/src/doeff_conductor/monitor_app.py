"""ADR 0002 — interactive run browser (Textual).

Browse runs (left), expand a workflow's phases/nodes (center, collapsible tree),
and inspect the highlighted node's situation (bottom). Live-refreshing and
READ-ONLY: it reads the same journals as the static views and mutates nothing.

Keys: ↑/↓ navigate · enter/space expand-collapse · r refresh · a all/running · q quit.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import DataTable, Footer, Header, Static, Tree
from textual.widgets.tree import TreeNode

from doeff_conductor.monitor import (
    _GLYPH,
    STATUS_PENDING,
    NodeView,
    _glyph,
    _short_node,
    node_situation,
    node_status_map,
    run_rows,
)

_NO_PHASE = "(no phase)"


def _node_label(view: NodeView) -> Text:
    text = Text()
    text.append_text(_glyph(view.status))
    text.append(f" {_short_node(view.node_id)} ", style="bold")
    text.append(view.status, style=_GLYPH.get(view.status, _GLYPH[STATUS_PENDING])[1])
    return text


class MonitorApp(App):
    """Interactive read-only conductor run monitor."""

    CSS = """
    Screen { layout: horizontal; }
    #runs { width: 38; border: round $primary; }
    #right { width: 1fr; }
    #tree { height: 1fr; border: round $primary; }
    #detail_scroll { height: 17; border: round $secondary; padding: 0 1; }
    """

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("a", "toggle_all", "All/running"),
    ]

    def __init__(
        self,
        state_dir: str | Path,
        *,
        workflow_id: str | None = None,
        only_running: bool = True,
        interval: float = 2.0,
    ) -> None:
        super().__init__()
        self.state_dir = state_dir
        self.only_running = only_running
        self.initial_run = workflow_id
        self.interval = interval
        self._run_ids: list[str] = []
        self._current_run: str | None = None
        self._node_widgets: dict[str, TreeNode] = {}
        self._views: dict[str, NodeView] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield DataTable(id="runs", cursor_type="row", zebra_stripes=True)
        with Vertical(id="right"):
            yield Tree("workflow", id="tree")
            with VerticalScroll(id="detail_scroll"):
                yield Static("", id="detail")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "conductor monitor"
        table = self.query_one("#runs", DataTable)
        table.add_column("RUN", key="name")
        table.add_column("ST", key="status")
        table.add_column("DONE", key="done")
        table.add_column("G", key="gates")
        self.refresh_runs(select=self.initial_run)
        self.set_interval(self.interval, self.tick)

    # ----------------------------------------------------------------- data #

    def refresh_runs(self, *, select: str | None = None) -> None:
        rows = run_rows(self.state_dir, only_running=self.only_running)
        table = self.query_one("#runs", DataTable)
        table.clear()
        self._run_ids = []
        for row in rows:
            table.add_row(row.name, row.status[:4], f"{row.done}/{row.total}", str(row.gates), key=row.id)
            self._run_ids.append(row.id)

        if not self._run_ids:
            self._current_run = None
            self.query_one("#tree", Tree).reset("(no runs)")
            self.query_one("#detail", Static).update(Text("No active runs.", style="dim"))
            return

        target = select if select in self._run_ids else None
        if target is None:
            target = self._current_run if self._current_run in self._run_ids else self._run_ids[0]
        table.move_cursor(row=self._run_ids.index(target))
        self.load_run(target, rebuild=True)

    def tick(self) -> None:
        rows = {r.id: r for r in run_rows(self.state_dir, only_running=self.only_running)}
        if set(rows) != set(self._run_ids):
            self.refresh_runs(select=self._current_run)
            return
        table = self.query_one("#runs", DataTable)
        for run_id, row in rows.items():
            try:
                table.update_cell(run_id, "status", row.status[:4])
                table.update_cell(run_id, "done", f"{row.done}/{row.total}")
                table.update_cell(run_id, "gates", str(row.gates))
            except Exception:
                pass
        if self._current_run:
            self.load_run(self._current_run, rebuild=False)

    def load_run(self, run_id: str, *, rebuild: bool) -> None:
        self._current_run = run_id
        views = node_status_map(self.state_dir, run_id)
        tree = self.query_one("#tree", Tree)
        if rebuild or set(views) != set(self._views):
            self._build_tree(tree, run_id, views)
        else:
            for node_id, node in self._node_widgets.items():
                if node_id in views:
                    node.set_label(_node_label(views[node_id]))
        self._views = views
        self._update_detail()

    def _build_tree(self, tree: Tree, run_id: str, views: dict[str, NodeView]) -> None:
        tree.reset(Text(f"RUN {run_id}", style="bold cyan"))
        tree.root.expand()
        self._node_widgets = {}
        by_phase: dict[str, list[NodeView]] = {}
        for view in views.values():
            by_phase.setdefault(view.phase or _NO_PHASE, []).append(view)
        if not by_phase:
            tree.root.add_leaf(Text("(no node activity yet)", style="dim"))
        for phase in sorted(by_phase):
            branch = tree.root.add(Text(phase, style="bold"), expand=True)
            for view in sorted(by_phase[phase], key=lambda v: v.node_id):
                leaf = branch.add_leaf(_node_label(view), data=view.node_id)
                self._node_widgets[view.node_id] = leaf

    def _update_detail(self) -> None:
        detail = self.query_one("#detail", Static)
        if not self._current_run:
            detail.update(Text("No active runs.", style="dim"))
            return
        tree = self.query_one("#tree", Tree)
        node = tree.cursor_node
        node_id = node.data if node is not None else None
        if not node_id or node_id not in self._views:
            detail.update(Text("Select a node (↑/↓) to see its situation.", style="dim"))
            return
        detail.update(node_situation(self.state_dir, self._current_run, self._views[node_id]))

    # --------------------------------------------------------------- events #

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        run_id = event.row_key.value if event.row_key is not None else None
        if run_id and run_id != self._current_run:
            self.load_run(run_id, rebuild=True)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        self._update_detail()

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        self._update_detail()

    # -------------------------------------------------------------- actions #

    def action_refresh(self) -> None:
        self.refresh_runs(select=self._current_run)

    def action_toggle_all(self) -> None:
        self.only_running = not self.only_running
        self.refresh_runs(select=self._current_run)
