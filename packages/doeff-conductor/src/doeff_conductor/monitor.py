"""ADR 0002 — read-only run monitor (multi-view).

Renders live node-lifecycle state by joining three READ-ONLY sources:
  * the observational ``progress-journal.jsonl`` (in-flight: running/parked),
  * the ``agent-journal.jsonl`` completion truth (``terminal_kind=succeeded``),
  * open gates from the overseer (parked, needing adjudication).

Status precedence (ADR 0002 D2): a validated agent-journal artifact wins over
any progress label, so a node whose worker pane reads "blocked" but whose
journal entry is ``succeeded`` renders DONE. agentd pane liveness is never read
here. This module performs NO run-state mutation.

Three views over the same data (chosen by the CLI ``--view`` flag):
  * ``overview`` — one summary row per run (counts + waiting gates),
  * ``compact``  — per-run: status counts, short node names, gates collapsed by
                   reason into a single actionable line,
  * ``tree``     — per-run phase→node tree + an expanded per-gate panel.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from doeff_conductor.journal import (
    PROGRESS_STATUS_FAILED,
    PROGRESS_STATUS_PARKED,
    PROGRESS_STATUS_RUNNING,
    PROGRESS_STATUS_SUCCEEDED,
    TERMINAL_KIND_SUCCEEDED,
    AgentJournal,
    ProgressJournal,
)
from doeff_conductor.overseer import list_open_gates
from doeff_conductor.replay_keying import node_identity_fingerprint

# Effective display status (post D2 join).
STATUS_DONE = "done"
STATUS_PENDING = "pending"

VIEWS = ("overview", "compact", "tree")

_GLYPH: dict[str, tuple[str, str]] = {
    STATUS_DONE: ("✓", "green"),
    PROGRESS_STATUS_SUCCEEDED: ("✓", "green"),
    PROGRESS_STATUS_RUNNING: ("●", "yellow"),
    PROGRESS_STATUS_FAILED: ("✗", "red"),
    PROGRESS_STATUS_PARKED: ("◔", "magenta"),
    STATUS_PENDING: ("·", "dim"),
}

# Order used for the per-run count summary.
_COUNT_ORDER = (STATUS_DONE, PROGRESS_STATUS_RUNNING, PROGRESS_STATUS_PARKED, PROGRESS_STATUS_FAILED)

_NODE_KINDS = {"agent", "workspace", "gate", "merge"}
_NO_PHASE = "(no phase)"


@dataclass(frozen=True, kw_only=True)
class NodeView:
    """Resolved per-node view for the monitor (D2 precedence already applied)."""

    node_id: str
    phase: str | None
    status: str
    session_id: str
    attempt: int
    node_identity: str


# --------------------------------------------------------------------------- #
# Core join (read-only) — shared by every view
# --------------------------------------------------------------------------- #


def _node_identity_for(run_id: str, node_id: str) -> str:
    """Recompute a node's identity from its node_id, mirroring agent_replay_decision.

    Lets the monitor join an open gate's ``node_id`` to the agent-journal's
    ``node_identity`` for runs that have no progress journal.
    """
    node_path = tuple(part for part in node_id.split("/") if part)
    return node_identity_fingerprint(workflow_name=run_id, node_path=node_path, loop_indices=())


def _succeeded_identities(state_dir: str | Path, workflow_id: str) -> set[str]:
    """node_identity set with a validated succeeded artifact (completion truth)."""
    try:
        entries = AgentJournal.for_run(workflow_id, state_dir=state_dir).latest_generation_entries()
    except Exception:  # a read-only monitor degrades, never crashes
        return set()
    return {e.node_identity for e in entries if e.terminal_kind == TERMINAL_KIND_SUCCEEDED}


def _open_gates(state_dir: str | Path, workflow_id: str) -> list[dict]:
    try:
        return list(list_open_gates(state_dir, workflow_id))
    except Exception:  # a read-only monitor degrades, never crashes
        return []


def node_status_map(state_dir: str | Path, workflow_id: str) -> dict[str, NodeView]:
    """Pure core: node_id -> NodeView with ADR 0002 D2 precedence applied.

    Completion artifact (agent-journal succeeded) overrides any progress label;
    otherwise the latest progress event's status is used. Runs with no progress
    journal fall back to their open-gate nodes so the view is not empty.
    """
    progress = ProgressJournal.for_run(workflow_id, state_dir=state_dir).latest_by_node()
    done = _succeeded_identities(state_dir, workflow_id)
    views: dict[str, NodeView] = {}
    for node_id, entry in progress.items():
        effective = STATUS_DONE if entry.node_identity in done else entry.status
        views[node_id] = NodeView(
            node_id=node_id,
            phase=entry.phase,
            status=effective,
            session_id=entry.session_id,
            attempt=entry.attempt,
            node_identity=entry.node_identity,
        )

    # Fallback for runs with no progress journal (created before the producer,
    # or whose nodes only surface via parked gates): include open-gate nodes.
    for gate in _open_gates(state_dir, workflow_id):
        node_id = str(gate.get("node_id") or "")
        if not node_id or node_id in views:
            continue
        identity = _node_identity_for(workflow_id, node_id)
        status = STATUS_DONE if identity in done else PROGRESS_STATUS_PARKED
        phase_value = gate.get("phase")
        views[node_id] = NodeView(
            node_id=node_id,
            phase=str(phase_value) if phase_value is not None else None,
            status=status,
            session_id="",
            attempt=0,
            node_identity=identity,
        )
    return views


# --------------------------------------------------------------------------- #
# Small presentation helpers
# --------------------------------------------------------------------------- #


def _short_node(node_id: str) -> str:
    """A scannable label: the meaningful tail (e.g. ``parallel[2]``), not the
    full ``run/0/Phase/0/parallel[2]/agent`` path."""
    parts = [p for p in node_id.split("/") if p]
    if parts and parts[-1] in _NODE_KINDS:
        parts = parts[:-1]
    return parts[-1] if parts else node_id


def _gate_reason(gate: dict) -> str:
    """The trailing ``:<reason>`` of the gate id (e.g. ``budget-exhausted``)."""
    gate_id = str(gate.get("gate_id", ""))
    if ":" in gate_id:
        return gate_id.rsplit(":", 1)[-1]
    return str(gate.get("reason", "parked"))


def _gate_option_names(gate: dict) -> list[str]:
    options = gate.get("options", []) or []
    return [str(o.get("name", "?")) if isinstance(o, dict) else str(o) for o in options]


def _stakes_summary(gate: dict) -> str:
    stakes = gate.get("stakes") or {}
    if not isinstance(stakes, dict):
        return ""
    bits = [str(stakes[k]) for k in ("verification_class", "reversibility", "blast_radius") if k in stakes]
    return " · ".join(bits)


def _glyph(status: str) -> Text:
    glyph, style = _GLYPH.get(status, _GLYPH[STATUS_PENDING])
    return Text(glyph, style=style)


def _counts(views: dict[str, NodeView]) -> Counter:
    return Counter(v.status for v in views.values())


def _counts_text(counts: Counter) -> Text:
    text = Text()
    for status in _COUNT_ORDER:
        n = counts.get(status, 0)
        if not n:
            continue
        if len(text):
            text.append("  ")
        text.append_text(_glyph(status))
        text.append(str(n), style=_GLYPH.get(status, _GLYPH[STATUS_PENDING])[1])
    return text or Text("—", style="dim")


def _run_header(workflow_id: str, name: str | None, run_status: str | None, views: dict[str, NodeView]) -> Text:
    done = sum(1 for v in views.values() if v.status == STATUS_DONE)
    header = Text()
    header.append("RUN ", style="bold cyan")
    header.append(name or workflow_id, style="bold")
    header.append(f"  ({workflow_id[:9]})", style="dim")
    if run_status:
        header.append(f"  {run_status}", style="bold")
    header.append(f"   {done}/{len(views)} done   ", style="dim")
    header.append_text(_counts_text(_counts(views)))
    return header


# --------------------------------------------------------------------------- #
# View: overview (one row per run)
# --------------------------------------------------------------------------- #


def render_overview(
    state_dir: str | Path,
    *,
    workflow_id: str | None = None,
    only_running: bool = True,
) -> RenderableType:
    from doeff_conductor.api import ConductorAPI

    api = ConductorAPI(str(state_dir))
    if workflow_id is not None:
        handle = api.get_workflow(workflow_id)
        handles = [handle] if handle is not None else []
    else:
        handles = list(api.list_workflows())

    table = Table(show_header=True, header_style="bold", expand=False)
    table.add_column("RUN", style="cyan", no_wrap=True)
    table.add_column("STATUS")
    table.add_column("DONE", justify="right")
    table.add_column("NODES")
    table.add_column("GATES", justify="right")

    total_gates = 0
    rows = 0
    for handle in handles:
        status_value = getattr(handle.status, "value", str(handle.status))
        if only_running and status_value not in ("running", "blocked", "pending"):
            continue
        rows += 1
        views = node_status_map(state_dir, handle.id)
        gates = _open_gates(state_dir, handle.id)
        total_gates += len(gates)
        done = sum(1 for v in views.values() if v.status == STATUS_DONE)
        gate_cell = Text(str(len(gates)) + (" ⚠" if gates else ""), style="magenta" if gates else "dim")
        table.add_row(
            handle.name,
            Text(status_value, style=_run_status_style(status_value)),
            f"{done}/{len(views)}" if views else "—",
            _counts_text(_counts(views)),
            gate_cell,
        )

    if rows == 0:
        return Panel(Text("No active runs.", style="dim"), title="conductor monitor")

    footer = Text()
    footer.append(f"{rows} run(s)", style="dim")
    if total_gates:
        footer.append(f" · {total_gates} gate(s) waiting ⚠", style="magenta")
    footer.append("  ·  drill in: ", style="dim")
    footer.append("conductor monitor <run> [--view compact|tree]", style="cyan")
    return Group(table, footer)


def _run_status_style(status_value: str) -> str:
    return {
        "running": "yellow",
        "blocked": "magenta",
        "done": "green",
        "error": "red",
        "pending": "dim",
    }.get(status_value, "white")


# --------------------------------------------------------------------------- #
# View: compact (per-run, status-grouped, gates collapsed by reason)
# --------------------------------------------------------------------------- #


def render_run(
    state_dir: str | Path,
    workflow_id: str,
    *,
    name: str | None = None,
    run_status: str | None = None,
) -> RenderableType:
    """Compact per-run view: header counts, phase→node lines (short names),
    and gates collapsed by reason into one actionable line each."""
    views = node_status_map(state_dir, workflow_id)
    lines: list[RenderableType] = [_run_header(workflow_id, name, run_status, views)]

    by_phase: dict[str, list[NodeView]] = {}
    for view in views.values():
        by_phase.setdefault(view.phase or _NO_PHASE, []).append(view)
    if not views:
        lines.append(Text("  (no node activity yet)", style="dim"))
    for phase in sorted(by_phase):
        phase_line = Text("  ")
        phase_line.append(phase, style="bold")
        lines.append(phase_line)
        for view in sorted(by_phase[phase], key=lambda v: v.node_id):
            row = Text("    ")
            row.append_text(_glyph(view.status))
            row.append(f" {_short_node(view.node_id)}", style="bold")
            row.append(f"  {view.status}", style=_GLYPH.get(view.status, _GLYPH[STATUS_PENDING])[1])
            if view.status == PROGRESS_STATUS_RUNNING and view.session_id:
                row.append(f"   attach: tmux attach -r -t {view.session_id}", style="dim")
            lines.append(row)

    gate_block = _render_gates_collapsed(state_dir, workflow_id)
    if gate_block is not None:
        lines.append(gate_block)
    return Group(*lines)


def _render_gates_collapsed(state_dir: str | Path, workflow_id: str) -> RenderableType | None:
    gates = _open_gates(state_dir, workflow_id)
    if not gates:
        return None

    # Group identical-reason gates so 10x budget-exhausted is one actionable line.
    by_reason: dict[str, list[dict]] = {}
    for gate in gates:
        by_reason.setdefault(_gate_reason(gate), []).append(gate)

    body = Text()
    for index, (reason, group) in enumerate(sorted(by_reason.items())):
        if index:
            body.append("\n")
        sample = group[0]
        summary = _stakes_summary(sample)
        opts = "|".join(_gate_option_names(sample))
        nodes = "  ".join(sorted(_short_node(str(g.get("node_id", ""))) for g in group))
        body.append("◔ ", style="magenta bold")
        body.append(f"{reason} x{len(group)}", style="magenta")
        if summary:
            body.append(f"   [{summary}]", style="dim")
        body.append(f"\n    nodes: {nodes}\n", style="dim")
        body.append(
            f"    → conductor gate answer {workflow_id} <gate-id> {{{opts}}}\n", style="dim"
        )
    title = f"Parked gates ({len(gates)}) — adjudicate  ·  --view tree for exact ids"
    return Panel(body, title=title, border_style="magenta")


# --------------------------------------------------------------------------- #
# View: tree (phase→node tree + expanded per-gate panel with exact commands)
# --------------------------------------------------------------------------- #


def render_run_tree(
    state_dir: str | Path,
    workflow_id: str,
    *,
    name: str | None = None,
    run_status: str | None = None,
) -> RenderableType:
    views = node_status_map(state_dir, workflow_id)
    tree = Tree(_run_header(workflow_id, name, run_status, views))
    by_phase: dict[str, list[NodeView]] = {}
    for view in views.values():
        by_phase.setdefault(view.phase or _NO_PHASE, []).append(view)
    if not by_phase:
        tree.add(Text("(no node activity yet)", style="dim"))
    for phase in sorted(by_phase):
        branch = tree.add(Text(phase, style="bold"))
        for view in sorted(by_phase[phase], key=lambda v: v.node_id):
            label = Text()
            label.append_text(_glyph(view.status))
            label.append(f" {view.node_id} ", style="bold")
            label.append(view.status, style=_GLYPH.get(view.status, _GLYPH[STATUS_PENDING])[1])
            if view.status == PROGRESS_STATUS_RUNNING and view.session_id:
                label.append(f"  [attach: tmux attach -r -t {view.session_id}]", style="dim")
            branch.add(label)

    renderables: list[RenderableType] = [tree]
    panel = _render_gates_expanded(state_dir, workflow_id)
    if panel is not None:
        renderables.append(panel)
    return Group(*renderables)


def _render_gates_expanded(state_dir: str | Path, workflow_id: str) -> RenderableType | None:
    gates = _open_gates(state_dir, workflow_id)
    if not gates:
        return None
    body = Text()
    for index, gate in enumerate(gates):
        if index:
            body.append("\n")
        gate_id = str(gate.get("gate_id", "?"))
        summary = _stakes_summary(gate)
        opts = "|".join(_gate_option_names(gate))
        body.append("◔ ", style="magenta bold")
        body.append(gate_id, style="magenta")
        if summary:
            body.append(f"   [{summary}]", style="dim")
        body.append("\n")
        body.append(f"    conductor gate answer {workflow_id} {gate_id} {{{opts}}}\n", style="dim")
    return Panel(body, title=f"Parked gates ({len(gates)}) — adjudicate", border_style="magenta")


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #


def render_dashboard(
    state_dir: str | Path,
    workflow_id: str | None = None,
    *,
    only_running: bool = True,
    view: str | None = None,
) -> RenderableType:
    """Render the chosen view. Auto-default: overview for all runs, compact for
    a single run."""
    if view is None:
        view = "overview" if workflow_id is None else "compact"
    if view == "overview":
        return render_overview(state_dir, workflow_id=workflow_id, only_running=only_running)

    render_one = render_run if view == "compact" else render_run_tree
    from doeff_conductor.api import ConductorAPI

    api = ConductorAPI(str(state_dir))
    if workflow_id is not None:
        handle = api.get_workflow(workflow_id)
        return render_one(
            state_dir,
            workflow_id,
            name=getattr(handle, "name", None),
            run_status=getattr(getattr(handle, "status", None), "value", None),
        )

    runs: list[RenderableType] = []
    for handle in api.list_workflows():
        status_value = getattr(handle.status, "value", str(handle.status))
        if only_running and status_value not in ("running", "blocked", "pending"):
            continue
        runs.append(render_one(state_dir, handle.id, name=handle.name, run_status=status_value))
    if not runs:
        return Panel(Text("No active runs.", style="dim"), title="conductor monitor")
    return Group(*runs)


# --------------------------------------------------------------------------- #
# Data helpers for the interactive (Textual) browser
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, kw_only=True)
class RunRow:
    """One row for the interactive runs list."""

    id: str
    name: str
    status: str
    done: int
    total: int
    counts: Counter
    gates: int


def run_rows(state_dir: str | Path, *, only_running: bool = True) -> list[RunRow]:
    """All runs (read-only) with their node counts and waiting-gate count."""
    from doeff_conductor.api import ConductorAPI

    api = ConductorAPI(str(state_dir))
    rows: list[RunRow] = []
    for handle in api.list_workflows():
        status_value = getattr(handle.status, "value", str(handle.status))
        if only_running and status_value not in ("running", "blocked", "pending"):
            continue
        views = node_status_map(state_dir, handle.id)
        rows.append(
            RunRow(
                id=handle.id,
                name=handle.name,
                status=status_value,
                done=sum(1 for v in views.values() if v.status == STATUS_DONE),
                total=len(views),
                counts=_counts(views),
                gates=len(_open_gates(state_dir, handle.id)),
            )
        )
    return rows


def _result_for_identity(state_dir: str | Path, run_id: str, node_identity: str) -> str | None:
    try:
        entries = AgentJournal.for_run(run_id, state_dir=state_dir).latest_generation_entries()
    except Exception:
        return None
    for entry in entries:
        if entry.node_identity == node_identity and entry.terminal_kind == TERMINAL_KIND_SUCCEEDED:
            import json

            try:
                text = json.dumps(entry.result_artifact, ensure_ascii=False)
            except (TypeError, ValueError):
                text = str(entry.result_artifact)
            return text if len(text) <= 240 else text[:237] + "…"
    return None


def capture_agent_output(session_id: str, *, lines: int = 200) -> str | None:
    """Captured agent tmux-pane text for display (ADR 0002 D3, read-only eyeball).

    Live pane via agentd's ``session.capture`` RPC; on failure (e.g. the session
    was cleaned) falls back to the persisted ``output_snippet`` in agentd.sqlite.
    Display only — never a status source. Fail-open: returns None on any error.
    """
    if not session_id:
        return None
    try:
        from doeff_agents import LazyAgentdClient

        return LazyAgentdClient().capture_session(session_id, lines=lines)
    except Exception:
        pass
    try:
        import os
        import sqlite3

        db = os.path.expanduser("~/.local/state/doeff/agentd.sqlite")
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute(
                "select output_snippet from agent_sessions where session_id = ?",
                (session_id,),
            ).fetchone()
        finally:
            con.close()
        if row and row[0]:
            return str(row[0])
    except Exception:
        pass
    return None


def node_situation(state_dir: str | Path, run_id: str, view: NodeView) -> Text:
    """The full 'situation' of one node for the detail pane (read-only)."""
    text = Text()
    text.append_text(_glyph(view.status))
    text.append(f" {view.node_id}\n", style="bold")
    text.append(f"phase: {view.phase or '—'}    ", style="dim")
    text.append("status: ", style="dim")
    text.append(f"{view.status}\n", style=_GLYPH.get(view.status, _GLYPH[STATUS_PENDING])[1])
    if view.attempt:
        text.append(f"attempt: {view.attempt}\n", style="dim")
    if view.session_id:
        text.append(f"session: {view.session_id}\n", style="dim")
        text.append(f"attach:  tmux attach -r -t {view.session_id}\n", style="cyan")

    gates = [g for g in _open_gates(state_dir, run_id) if str(g.get("node_id", "")) == view.node_id]
    for gate in gates:
        text.append("\nPARKED — adjudicate:\n", style="magenta bold")
        summary = _stakes_summary(gate)
        if summary:
            text.append(f"  {summary}\n", style="dim")
        gate_id = str(gate.get("gate_id", "?"))
        for opt in _gate_option_names(gate):
            text.append(f"  conductor gate answer {run_id} {gate_id} {opt}\n", style="cyan")

    if view.status == STATUS_DONE:
        result = _result_for_identity(state_dir, run_id, view.node_identity)
        if result is not None:
            text.append("\nresult artifact:\n", style="green")
            text.append(f"  {result}\n", style="green")
    return text
