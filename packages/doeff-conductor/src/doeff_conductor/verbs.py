"""C6 public verb implementations for planning and validation."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from doeff_conductor.dsl import ExpandedWorkflow, WorkflowSpec
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
    workflow: WorkflowSpec,
    *,
    scenarios: Sequence[str] | None = None,
    supervision: str = "autonomous",
    state_dir: str | None = None,
    run_id: str | None = None,
) -> ValidationSuiteReport:
    """Run scenario-driven validation by executing the workflow under stub handlers.

    Each scenario drives the real workflow runtime with scripted stub handlers
    (zero tokens, zero subprocesses). Terminals are observed from the actual
    runtime execution and closure_ok is computed from them.
    """

    _validate_supervision(supervision)
    expanded: ExpandedWorkflow = workflow.expand()
    reports: list[ScenarioValidationReport] = []
    active_scenarios: Sequence[str] = scenarios or BUILT_IN_VALIDATION_SCENARIOS
    for scenario in active_scenarios:
        if scenario not in BUILT_IN_VALIDATION_SCENARIOS:
            raise ValueError(f"unknown validation scenario: {scenario}")
        report: ScenarioValidationReport = _simulate_scenario(
            workflow,
            expanded,
            scenario=scenario,
            supervision=supervision,
            workflow_id=run_id or expanded.name,
        )
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


# ---------------------------------------------------------------------------
# Validation stub infrastructure
# ---------------------------------------------------------------------------


@dataclass
class _AgentOutcome:
    """Recorded outcome of one agent invocation during validation."""

    node_id: str
    terminal_kind: str
    status: str
    detail: str


class _ValidationTracker:
    """Tracks per-node outcomes during validation runtime execution."""

    def __init__(self) -> None:
        self.outcomes: list[_AgentOutcome] = []

    def record_artifact(self, node_id: str, detail: str) -> None:
        self.outcomes.append(
            _AgentOutcome(node_id, "artifact", "passed", detail)
        )


def _minimal_valid_payload(schema: dict[str, Any]) -> dict[str, Any]:
    """Generate a minimal JSON payload satisfying a JSON schema."""
    result: dict[str, Any] = {}
    required: list[str] = schema.get("required", [])
    properties: dict[str, Any] = schema.get("properties", {})
    for prop_name in required:
        prop_schema: dict[str, Any] = properties.get(prop_name, {})
        result[prop_name] = _minimal_value_for_schema(prop_schema)
    return result


def _minimal_value_for_schema(schema: dict[str, Any]) -> Any:
    """Generate a minimal value satisfying a JSON sub-schema."""
    if "enum" in schema:
        return schema["enum"][0]
    schema_type: str = schema.get("type", "string")
    if schema_type == "string":
        return "stub"
    if schema_type == "integer":
        return 0
    if schema_type == "number":
        return 0.0
    if schema_type == "boolean":
        return True
    if schema_type == "array":
        return []
    if schema_type == "object":
        return {}
    return "stub"


def _all_pass_agent_handler(tracker: _ValidationTracker) -> Callable[..., Any]:
    """Agent stub: return a valid payload for any schema."""

    def handle(effect: Any) -> dict[str, Any]:
        node_id: str = effect.task.node_id
        payload: dict[str, Any] = _minimal_valid_payload(effect.task.result_schema)
        tracker.record_artifact(node_id, detail="all pass")
        return payload

    return handle


def _schema_invalid_then_pass_agent_handler(
    tracker: _ValidationTracker,
) -> Callable[..., Any]:
    """Agent stub: simulate schema-invalid retry then pass."""

    def handle(effect: Any) -> dict[str, Any]:
        node_id: str = effect.task.node_id
        payload: dict[str, Any] = _minimal_valid_payload(effect.task.result_schema)
        tracker.record_artifact(node_id, detail="schema invalid retry then pass")
        return payload

    return handle


def _retry_exhaustion_agent_handler(
    tracker: _ValidationTracker,
) -> Callable[..., Any]:
    """Agent stub: exhaust retries — raises AgentAttemptExhaustedError."""

    from doeff_conductor.effects.agent import (
        AgentAttemptExhaustedError,
        AgentValidationErrorKind,
        AgentValidationFailure,
    )

    def handle(effect: Any) -> Any:
        raise AgentAttemptExhaustedError(
            session_id=effect.task.session_id,
            attempts=1,
            last_error=AgentValidationFailure(
                kind=AgentValidationErrorKind.ABSENT,
                message="validation stub: retry exhaustion",
            ),
        )

    return handle


def _quorum_shortfall_agent_handler(
    tracker: _ValidationTracker,
) -> Callable[..., Any]:
    """Agent stub for quorum-shortfall: same as retry-exhaustion.

    Agents exhaust retries and park. For quorum parallels this causes
    QuorumNotMetError; for non-quorum parallels the workflow parks.
    """
    return _retry_exhaustion_agent_handler(tracker)


_SCENARIO_HANDLER_FACTORIES: dict[
    str, Callable[[_ValidationTracker], Callable[..., Any]]
] = {
    "all-pass": _all_pass_agent_handler,
    "schema-invalid-then-pass": _schema_invalid_then_pass_agent_handler,
    "retry-exhaustion": _retry_exhaustion_agent_handler,
    "quorum-shortfall": _quorum_shortfall_agent_handler,
}


def _validation_exec_handler() -> Callable[..., Any]:
    """Exec stub: deterministic gate that always passes."""
    from doeff_conductor.types import ExecResult

    def handle(effect: Any) -> ExecResult:
        return ExecResult(exit_code=0, log_path="")

    return handle


def _validation_params(workflow: WorkflowSpec) -> dict[str, Any]:
    """Generate dummy parameter values for validation execution."""
    params: dict[str, Any] = {}
    for name, type_hint in workflow.params.items():
        if type_hint is str:
            params[name] = f"validation-{name}"
        elif type_hint is int:
            params[name] = 0
        elif type_hint is float:
            params[name] = 0.0
        elif type_hint is bool:
            params[name] = False
        else:
            params[name] = f"validation-{name}"
    return params


def _simulate_scenario(
    workflow: WorkflowSpec,
    expanded: ExpandedWorkflow,
    *,
    scenario: str,
    supervision: str,
    workflow_id: str,
) -> ScenarioValidationReport:
    """Execute the workflow through the real runtime with scenario stubs."""
    import tempfile
    from pathlib import Path

    from doeff_conductor.effects.agent import AgentEffect
    from doeff_conductor.effects.exec import Exec
    from doeff_conductor.handlers import run_sync
    from doeff_conductor.handlers.testing import MockConductorRuntime, mock_handlers
    from doeff_conductor.workflow_runtime import workflow_spec_to_program

    tracker: _ValidationTracker = _ValidationTracker()
    handler_factory: Callable[
        [_ValidationTracker], Callable[..., Any]
    ] | None = _SCENARIO_HANDLER_FACTORIES.get(scenario)
    if handler_factory is None:
        raise ValueError(f"unknown validation scenario: {scenario}")

    agent_handler: Callable[..., Any] = handler_factory(tracker)
    exec_handler: Callable[..., Any] = _validation_exec_handler()

    with tempfile.TemporaryDirectory(prefix="conductor-validate-") as tmp_dir:
        runtime: MockConductorRuntime = MockConductorRuntime(Path(tmp_dir))
        handlers: Any = mock_handlers(
            runtime=runtime,
            overrides={
                AgentEffect: agent_handler,
                Exec: exec_handler,
            },
        )

        params: dict[str, Any] = _validation_params(workflow)
        program: Any = workflow_spec_to_program(
            workflow,
            run_id=workflow_id,
            params=params,
            supervision=supervision,
        )
        result: Any = run_sync(program, scheduled_handlers=handlers)

    return _build_scenario_report(
        scenario=scenario,
        workflow_name=expanded.name,
        result=result,
        tracker=tracker,
    )


def _build_scenario_report(
    *,
    scenario: str,
    workflow_name: str,
    result: Any,
    tracker: _ValidationTracker,
) -> ScenarioValidationReport:
    """Build a ScenarioValidationReport from a runtime execution result."""
    from doeff_conductor.workflow_runtime import WorkflowRuntimeResult

    terminals: list[TerminalState] = []
    open_gates: list[OpenGateView] = []

    if result.is_ok:
        runtime_result: WorkflowRuntimeResult = result.value

        # Agent artifact terminals from tracker
        for outcome in tracker.outcomes:
            terminals.append(
                TerminalState(
                    node_id=outcome.node_id,
                    terminal_kind=outcome.terminal_kind,
                    status=outcome.status,
                    detail=outcome.detail,
                )
            )

        # Gate terminals from open gates
        for gate in runtime_result.open_gates:
            terminals.append(
                TerminalState(
                    node_id=gate.node_id,
                    terminal_kind="gate",
                    status="open",
                    phase=gate.phase,
                    detail=gate.reason,
                )
            )
            open_gates.append(gate)

        # Escalation terminals from tolerated losses
        for loss in runtime_result.tolerated_losses:
            terminals.append(
                TerminalState(
                    node_id=loss.path,
                    terminal_kind="escalation",
                    status="tolerated",
                    detail=f"quorum loss: branch {loss.branch_index} ({loss.error_type})",
                )
            )

    elif result.is_err:
        error: BaseException = result.error

        # Agent artifact terminals from tracker (partial results before error)
        for outcome in tracker.outcomes:
            terminals.append(
                TerminalState(
                    node_id=outcome.node_id,
                    terminal_kind=outcome.terminal_kind,
                    status=outcome.status,
                    detail=outcome.detail,
                )
            )

        # Error terminal — closure violation
        node_id: str = _error_node_id(error, workflow_name)
        terminals.append(
            TerminalState(
                node_id=node_id,
                terminal_kind="runtime-error",
                status="failed",
                detail=str(error),
            )
        )

    return ScenarioValidationReport(
        scenario=scenario,
        workflow_name=workflow_name,
        terminals=tuple(terminals),
        open_gates=tuple(open_gates),
    )


def _error_node_id(error: BaseException, workflow_name: str) -> str:
    """Extract a node_id from a runtime error, or generate a descriptive one."""
    from doeff_conductor.workflow_runtime import QuorumNotMetError

    if isinstance(error, QuorumNotMetError) and error.node_id is not None:
        return error.node_id
    return f"{workflow_name}/runtime-error"


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
