"""C6 public verb implementations for planning and validation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from doeff_conductor.dsl import ExpandedNode, ExpandedWorkflow, WorkflowSpec
from doeff_conductor.effects.dsl import AgentCall
from doeff_conductor.environment import (
    DEFAULT_ROUTER_POLICY,
    DEFAULT_SITE_CAPABILITIES,
    ProfileBinding,
    ProfileRegistry,
    describe_author_environment,
    load_profile_registry_from_env,
)
from doeff_conductor.interpreters import plan_interpreter, validation_interpreter
from doeff_conductor.overseer import (
    GateOption,
    OpenGateView,
    ProgressEvent,
    RunStateView,
    make_progress_event,
    save_run_state,
)
from doeff_conductor.replay_keying import agent_cache_key

ALLOWED_TERMINAL_KINDS: frozenset[str] = frozenset(
    {"artifact", "verdict", "escalation", "gate"}
)
BUILT_IN_VALIDATION_SCENARIOS: tuple[str, ...] = (
    "all-pass",
    "schema-invalid-then-pass",
    "retry-exhaustion",
    "quorum-shortfall",
    "merge-conflict",
    "loop-exhaustion",
)
SUPPORTED_SUPERVISION_POLICIES: tuple[str, ...] = ("autonomous", "phase-checkpoints")


@dataclass(frozen=True)
class BindingPlanRow:
    """One statically resolved agent binding."""

    node_id: str
    role: str
    profile: str
    fingerprint: str
    phase: str | None
    verification_class: str
    estimated_budget_units: int
    resolution_source: str
    label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "role": self.role,
            "profile": self.profile,
            "fingerprint": self.fingerprint,
            "phase": self.phase,
            "verification_class": self.verification_class,
            "estimated_budget_units": self.estimated_budget_units,
            "resolution_source": self.resolution_source,
            "label": self.label,
        }


@dataclass(frozen=True)
class BindingPlan:
    """Overseer approval artifact for a workflow launch."""

    workflow_name: str
    interpreter: str
    rows: tuple[BindingPlanRow, ...]
    estimated_budget_units: int
    totals_by_profile: Mapping[str, int]
    capabilities_satisfied: bool
    missing_capabilities: tuple[str, ...]
    supervision: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_name": self.workflow_name,
            "interpreter": self.interpreter,
            "rows": [row.to_dict() for row in self.rows],
            "estimated_budget_units": self.estimated_budget_units,
            "totals_by_profile": dict(self.totals_by_profile),
            "capabilities_satisfied": self.capabilities_satisfied,
            "missing_capabilities": list(self.missing_capabilities),
            "supervision": self.supervision,
        }


@dataclass(frozen=True)
class TerminalState:
    """Terminal state reported by validation scenarios."""

    node_id: str
    terminal_kind: str
    status: str
    phase: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "terminal_kind": self.terminal_kind,
            "status": self.status,
            "phase": self.phase,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ScenarioValidationReport:
    """Validation result for one scenario."""

    scenario: str
    workflow_name: str
    terminals: tuple[TerminalState, ...]
    open_gates: tuple[OpenGateView, ...]

    def to_dict(self) -> dict[str, Any]:
        closure_ok: bool = all(
            terminal.terminal_kind in ALLOWED_TERMINAL_KINDS
            for terminal in self.terminals
        )
        return {
            "scenario": self.scenario,
            "workflow_name": self.workflow_name,
            "terminals": [terminal.to_dict() for terminal in self.terminals],
            "open_gates": [gate.to_dict() for gate in self.open_gates],
            "closure_ok": closure_ok,
        }


@dataclass(frozen=True)
class ValidationSuiteReport:
    """Validation report over all requested scenarios."""

    workflow_name: str
    interpreter: str
    scenarios: tuple[ScenarioValidationReport, ...]
    supervision: str

    def to_dict(self) -> dict[str, Any]:
        closure_ok: bool = all(
            scenario.to_dict()["closure_ok"]
            for scenario in self.scenarios
        )
        return {
            "workflow_name": self.workflow_name,
            "interpreter": self.interpreter,
            "supervision": self.supervision,
            "scenarios": [scenario.to_dict() for scenario in self.scenarios],
            "closure_ok": closure_ok,
        }


def plan_workflow(
    workflow: WorkflowSpec | ExpandedWorkflow,
    *,
    registry: ProfileRegistry | None = None,
    site_capabilities: tuple[str, ...] = DEFAULT_SITE_CAPABILITIES,
    supervision: str = "autonomous",
) -> BindingPlan:
    """Resolve the static binding plan without executing workflow effects."""

    _validate_supervision(supervision)
    expanded: ExpandedWorkflow = _ensure_expanded(workflow)
    active_registry: ProfileRegistry = registry or load_profile_registry_from_env()
    rows: list[BindingPlanRow] = []
    missing_capabilities: set[str] = set()
    totals_by_profile: dict[str, int] = {}

    for node in expanded.nodes:
        if node.kind != "agent":
            continue
        if not isinstance(node.effect, AgentCall):
            raise ValueError(f"agent node {node.node_id!r} does not carry AgentCall")

        profile_name, source = resolve_agent_profile(
            node.effect,
            roles=expanded.roles,
            registry=active_registry,
        )
        profile: ProfileBinding = active_registry.resolve(profile_name)
        required_capabilities: tuple[str, ...] = _required_capabilities(
            node.effect,
            expanded.roles,
        )
        missing_capabilities.update(
            capability
            for capability in required_capabilities
            if capability not in profile.capabilities and capability not in site_capabilities
        )
        fingerprint: str = agent_cache_key(
            prompt=node.effect.prompt,
            schema=node.effect.schema,
            resolved_identity=profile.resolved_identity,
        )
        estimated_budget_units: int = node.budget_units or profile.budget_units
        totals_by_profile[profile_name] = (
            totals_by_profile.get(profile_name, 0) + estimated_budget_units
        )
        rows.append(
            BindingPlanRow(
                node_id=node.node_id,
                role=node.effect.role,
                profile=profile_name,
                fingerprint=fingerprint,
                phase=node.phase,
                verification_class=node.effect.verification_class,
                estimated_budget_units=estimated_budget_units,
                resolution_source=source,
                label=node.effect.label,
            )
        )

    return BindingPlan(
        workflow_name=expanded.name,
        interpreter=plan_interpreter.name,
        rows=tuple(rows),
        estimated_budget_units=sum(row.estimated_budget_units for row in rows),
        totals_by_profile=totals_by_profile,
        capabilities_satisfied=not missing_capabilities,
        missing_capabilities=tuple(sorted(missing_capabilities)),
        supervision=supervision,
    )


def resolve_agent_profile(
    effect: AgentCall,
    *,
    roles: Mapping[str, Mapping[str, Any]],
    registry: ProfileRegistry,
) -> tuple[str, str]:
    """Resolve the fixed D7 profile cascade in one place."""

    if effect.profile is not None:
        registry.resolve(effect.profile)
        return effect.profile, "explicit"

    role_spec: Mapping[str, Any] = roles[effect.role]
    role_profile: object | None = role_spec.get("profile")
    if isinstance(role_profile, str) and role_profile:
        registry.resolve(role_profile)
        return role_profile, "role"

    routed_profile: str | None = DEFAULT_ROUTER_POLICY.get(effect.verification_class)
    if routed_profile is not None:
        registry.resolve(routed_profile)
        return routed_profile, "router"

    registry.resolve(registry.default_profile)
    return registry.default_profile, "interpreter-env"


def validate_workflow(
    workflow: WorkflowSpec | ExpandedWorkflow,
    *,
    scenarios: Sequence[str] | None = None,
    supervision: str = "autonomous",
    state_dir: str | None = None,
    run_id: str | None = None,
) -> ValidationSuiteReport:
    """Run scenario-driven stub validation and assert closure."""

    _validate_supervision(supervision)
    expanded: ExpandedWorkflow = _ensure_expanded(workflow)
    reports: list[ScenarioValidationReport] = []
    active_scenarios: Sequence[str] = scenarios or BUILT_IN_VALIDATION_SCENARIOS
    for scenario in active_scenarios:
        if scenario not in BUILT_IN_VALIDATION_SCENARIOS:
            raise ValueError(f"unknown validation scenario: {scenario}")
        report: ScenarioValidationReport = _simulate_scenario(
            expanded,
            scenario=scenario,
            supervision=supervision,
            workflow_id=run_id or expanded.name,
        )
        assert_validation_closure(report)
        reports.append(report)

    suite: ValidationSuiteReport = ValidationSuiteReport(
        workflow_name=expanded.name,
        interpreter=validation_interpreter.name,
        scenarios=tuple(reports),
        supervision=supervision,
    )

    if state_dir is not None and run_id is not None:
        save_run_state(state_dir, _run_state_from_reports(run_id, expanded.name, suite))

    return suite


def assert_validation_closure(report: ScenarioValidationReport) -> None:
    """Fail loudly when a validation terminal violates the closure law."""

    for terminal in report.terminals:
        if terminal.terminal_kind not in ALLOWED_TERMINAL_KINDS:
            raise ValueError(
                f"closure law violation in {report.scenario}: "
                f"{terminal.node_id} ended as {terminal.terminal_kind!r}"
            )


def describe_environment() -> dict[str, Any]:
    return describe_author_environment()


def _ensure_expanded(workflow: WorkflowSpec | ExpandedWorkflow) -> ExpandedWorkflow:
    if isinstance(workflow, ExpandedWorkflow):
        return workflow
    return workflow.expand()


def _required_capabilities(
    effect: AgentCall,
    roles: Mapping[str, Mapping[str, Any]],
) -> tuple[str, ...]:
    role_spec: Mapping[str, Any] = roles[effect.role]
    raw_capabilities: object = role_spec.get("requires", ())
    if raw_capabilities == ():
        raw_capabilities = role_spec.get("capabilities", ())
    if not isinstance(raw_capabilities, Iterable) or isinstance(raw_capabilities, (str, bytes)):
        raise ValueError(f"role {effect.role!r} capabilities must be a sequence")
    capabilities: tuple[str, ...] = tuple(str(item) for item in raw_capabilities)
    return ("schema-validation", *capabilities)


def _validate_supervision(supervision: str) -> None:
    if supervision not in SUPPORTED_SUPERVISION_POLICIES:
        raise ValueError(f"unsupported supervision policy: {supervision}")


def _simulate_scenario(
    expanded: ExpandedWorkflow,
    *,
    scenario: str,
    supervision: str,
    workflow_id: str,
) -> ScenarioValidationReport:
    terminals: list[TerminalState] = []
    open_gates: list[OpenGateView] = []

    for node in expanded.nodes:
        terminal: TerminalState = _terminal_for_node(node, scenario)
        terminals.append(terminal)
        if terminal.terminal_kind == "gate":
            open_gates.append(
                _open_gate_for_terminal(
                    workflow_id=workflow_id,
                    terminal=terminal,
                    reason=terminal.detail or scenario,
                    expanded=expanded,
                )
            )

    if supervision == "phase-checkpoints":
        for phase_name, phase in expanded.phases.items():
            checkpoint_terminal: TerminalState = TerminalState(
                node_id=f"checkpoint:{phase_name}",
                terminal_kind="gate",
                status="open",
                phase=phase_name,
                detail="phase checkpoint",
            )
            terminals.append(checkpoint_terminal)
            open_gates.append(
                OpenGateView(
                    gate_id=f"{workflow_id}:checkpoint:{phase_name}",
                    workflow_id=workflow_id,
                    node_id=checkpoint_terminal.node_id,
                    phase=phase_name,
                    reason="phase checkpoint",
                    stakes={
                        "phase": phase_name,
                        "level": phase.stakes,
                        "verification_class": "checkpoint",
                        "blast_radius": "dependent-subtree",
                        "reversibility": "abortable",
                    },
                    options=_checkpoint_options(),
                )
            )

    return ScenarioValidationReport(
        scenario=scenario,
        workflow_name=expanded.name,
        terminals=tuple(terminals),
        open_gates=tuple(open_gates),
    )


def _terminal_for_node(node: ExpandedNode, scenario: str) -> TerminalState:
    if scenario == "retry-exhaustion" and node.kind == "agent":
        return TerminalState(
            node_id=node.node_id,
            terminal_kind="gate",
            status="open",
            phase=node.phase,
            detail="agent retry exhaustion",
        )
    if scenario == "quorum-shortfall" and node.kind in {"parallel", "parallel-for"}:
        return TerminalState(
            node_id=node.node_id,
            terminal_kind="gate",
            status="open",
            phase=node.phase,
            detail="quorum shortfall",
        )
    if scenario == "merge-conflict" and node.kind == "merge":
        return TerminalState(
            node_id=node.node_id,
            terminal_kind="gate",
            status="open",
            phase=node.phase,
            detail="merge conflict",
        )
    if scenario == "loop-exhaustion" and node.kind == "loop":
        return TerminalState(
            node_id=node.node_id,
            terminal_kind="gate",
            status="open",
            phase=node.phase,
            detail="loop predicate exhaustion",
        )
    if node.kind == "agent":
        detail: str = "schema invalid retry then pass" if scenario == "schema-invalid-then-pass" else scenario
        return TerminalState(
            node_id=node.node_id,
            terminal_kind="artifact",
            status="passed",
            phase=node.phase,
            detail=detail,
        )
    if node.kind == "gate":
        return TerminalState(
            node_id=node.node_id,
            terminal_kind="artifact",
            status="passed",
            phase=node.phase,
            detail="deterministic gate passed",
        )
    return TerminalState(
        node_id=node.node_id,
        terminal_kind="artifact",
        status="passed",
        phase=node.phase,
        detail=f"{node.kind} closed",
    )


def _open_gate_for_terminal(
    *,
    workflow_id: str,
    terminal: TerminalState,
    reason: str,
    expanded: ExpandedWorkflow,
) -> OpenGateView:
    stakes: dict[str, Any] = {
        "phase": terminal.phase,
        "level": _phase_stakes(expanded, terminal.phase),
        "verification_class": "unknown",
        "blast_radius": "dependent-subtree",
        "reversibility": "abortable",
    }
    return OpenGateView(
        gate_id=f"{workflow_id}:{terminal.node_id}",
        workflow_id=workflow_id,
        node_id=terminal.node_id,
        phase=terminal.phase,
        reason=reason,
        stakes=stakes,
        options=_failure_gate_options(),
    )


def _phase_stakes(expanded: ExpandedWorkflow, phase_name: str | None) -> str:
    if phase_name is None:
        return "normal"
    phase = expanded.phases.get(phase_name)
    if phase is None:
        return "normal"
    return phase.stakes


def _failure_gate_options() -> tuple[GateOption, ...]:
    return (
        GateOption(
            name="proceed",
            outcome="resume",
            description="Resume the parked subtree after the blocking condition is cleared.",
        ),
        GateOption(
            name="redirect",
            outcome="resume",
            description="Edit workflow state or inputs, then resume from the journal prefix.",
        ),
        GateOption(
            name="abort",
            outcome="abort",
            description="Abort the run and preserve the gate as the terminal outcome.",
        ),
    )


def _checkpoint_options() -> tuple[GateOption, ...]:
    return (
        GateOption(
            name="proceed",
            outcome="resume",
            description="Accept the phase artifact summaries and continue.",
        ),
        GateOption(
            name="redirect",
            outcome="resume",
            description="Edit workflow state or inputs, then resume from the checkpoint.",
        ),
        GateOption(
            name="abort",
            outcome="abort",
            description="Abort the run at this checkpoint.",
        ),
    )


def _run_state_from_reports(
    workflow_id: str,
    workflow_name: str,
    suite: ValidationSuiteReport,
) -> RunStateView:
    events: list[ProgressEvent] = []
    gates: list[OpenGateView] = []
    sequence: int = 0
    for report in suite.scenarios:
        for terminal in report.terminals:
            sequence += 1
            events.append(
                make_progress_event(
                    sequence=sequence,
                    workflow_id=workflow_id,
                    node_id=terminal.node_id,
                    phase=terminal.phase,
                    status=terminal.status,
                    message=f"{report.scenario}: {terminal.detail or terminal.terminal_kind}",
                    terminal_kind=terminal.terminal_kind,
                )
            )
        gates.extend(report.open_gates)
    return RunStateView(
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        events=tuple(events),
        open_gates=tuple(gates),
        supervision=suite.supervision,
    )
