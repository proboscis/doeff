"""ADR 0002 — read-only run monitor.

Renders live node-lifecycle state by joining three READ-ONLY sources:
  * the observational ``progress-journal.jsonl`` (in-flight: running/parked),
  * the ``agent-journal.jsonl`` completion truth (``terminal_kind=succeeded``),
  * open gates from the overseer (parked, needing adjudication).

Status precedence (ADR 0002 D2): a validated agent-journal artifact wins over
any progress label, so a node whose worker pane reads "blocked" but whose
journal entry is ``succeeded`` renders DONE. agentd pane liveness is never read
here. This module performs NO run-state mutation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rich.console import Group, RenderableType
from rich.panel import Panel
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

_GLYPH: dict[str, tuple[str, str]] = {
    STATUS_DONE: ("✓", "green"),
    PROGRESS_STATUS_SUCCEEDED: ("✓", "green"),
    PROGRESS_STATUS_RUNNING: ("●", "yellow"),
    PROGRESS_STATUS_FAILED: ("✗", "red"),
    PROGRESS_STATUS_PARKED: ("◔", "magenta"),
    STATUS_PENDING: ("·", "dim"),
}

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


def node_status_map(state_dir: str | Path, workflow_id: str) -> dict[str, NodeView]:
    """Pure core: node_id -> NodeView with ADR 0002 D2 precedence applied.

    Completion artifact (agent-journal succeeded) overrides any progress label;
    otherwise the latest progress event's status is used.
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

    # Fallback for runs with no progress journal (e.g. created before the
    # producer, or any run whose nodes only surface via parked gates): include
    # open-gate nodes so the tree is not empty. The gate carries node_id; we
    # recompute its identity to mark done-vs-parked against the agent-journal.
    try:
        gates = list_open_gates(state_dir, workflow_id)
    except Exception:  # a read-only monitor degrades, never crashes
        gates = []
    for gate in gates:
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


def _glyph(status: str) -> Text:
    glyph, style = _GLYPH.get(status, _GLYPH[STATUS_PENDING])
    return Text(glyph, style=style)


def _node_label(view: NodeView) -> Text:
    label = Text()
    label.append_text(_glyph(view.status))
    label.append(f" {view.node_id} ", style="bold")
    label.append(view.status, style=_GLYPH.get(view.status, _GLYPH[STATUS_PENDING])[1])
    if view.status == PROGRESS_STATUS_RUNNING and view.session_id:
        # ADR 0002 D3: surface the read-only attach command (never a status source).
        label.append(f"  [attach: tmux attach -r -t {view.session_id}]", style="dim")
    return label


def render_run(
    state_dir: str | Path,
    workflow_id: str,
    *,
    name: str | None = None,
    run_status: str | None = None,
) -> RenderableType:
    """Render one run as a phase→node tree + a parked-gate panel (read-only)."""
    views = node_status_map(state_dir, workflow_id)

    header = Text()
    header.append("RUN ", style="bold cyan")
    header.append(name or workflow_id, style="bold")
    header.append(f"  ({workflow_id[:7]})", style="dim")
    if run_status:
        header.append(f"  {run_status}", style="bold")
    done = sum(1 for v in views.values() if v.status == STATUS_DONE)
    header.append(f"   {done}/{len(views)} done", style="dim")

    tree = Tree(header)
    by_phase: dict[str, list[NodeView]] = {}
    for view in views.values():
        by_phase.setdefault(view.phase or _NO_PHASE, []).append(view)
    if not by_phase:
        tree.add(Text("(no node activity yet)", style="dim"))
    for phase in sorted(by_phase):
        phase_branch = tree.add(Text(phase, style="bold"))
        for view in sorted(by_phase[phase], key=lambda v: v.node_id):
            phase_branch.add(_node_label(view))

    renderables: list[RenderableType] = [tree]
    gate_panel = _render_gates(state_dir, workflow_id)
    if gate_panel is not None:
        renderables.append(gate_panel)
    return Group(*renderables)


def _render_gates(state_dir: str | Path, workflow_id: str) -> RenderableType | None:
    try:
        gates = list_open_gates(state_dir, workflow_id)
    except Exception:  # read-only monitor degrades on a corrupt gate, never crashes
        return None
    if not gates:
        return None
    body = Text()
    for index, gate in enumerate(gates):
        if index:
            body.append("\n")
        gate_id = str(gate.get("gate_id", "?"))
        stakes = gate.get("stakes") or {}
        summary_bits: list[str] = []
        if isinstance(stakes, dict):
            for key in ("verification_class", "reversibility", "blast_radius"):
                if key in stakes:
                    summary_bits.append(str(stakes[key]))
        options = gate.get("options", []) or []
        opt_names = [
            str(opt.get("name", "?")) if isinstance(opt, dict) else str(opt) for opt in options
        ]
        body.append("◔ ", style="magenta bold")
        body.append(gate_id, style="magenta")
        if summary_bits:
            body.append(f"   [{'  '.join(summary_bits)}]", style="dim")
        body.append("\n")
        # ADR 0002 D4: surface the exact write command (options inline); never write.
        body.append(
            f"    conductor gate answer {workflow_id} {gate_id} " + "{" + "|".join(opt_names) + "}\n",
            style="dim",
        )
    return Panel(body, title=f"Parked gates ({len(gates)}) — adjudicate", border_style="magenta")


def render_dashboard(
    state_dir: str | Path,
    workflow_id: str | None = None,
    *,
    only_running: bool = True,
) -> RenderableType:
    """Render one run (if workflow_id given) or all (running) runs."""
    from doeff_conductor.api import ConductorAPI

    if workflow_id is not None:
        api = ConductorAPI(str(state_dir))
        handle = api.get_workflow(workflow_id)
        return render_run(
            state_dir,
            workflow_id,
            name=getattr(handle, "name", None),
            run_status=getattr(getattr(handle, "status", None), "value", None),
        )

    api = ConductorAPI(str(state_dir))
    handles = api.list_workflows()
    runs: list[RenderableType] = []
    for handle in handles:
        status_value = getattr(handle.status, "value", str(handle.status))
        if only_running and status_value not in ("running", "blocked", "pending"):
            continue
        runs.append(
            render_run(state_dir, handle.id, name=handle.name, run_status=status_value)
        )
    if not runs:
        return Panel(Text("No active runs.", style="dim"), title="conductor monitor")
    return Group(*runs)
