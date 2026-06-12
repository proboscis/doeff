"""Closed workflow DSL IR and expansion-time checks for doeff-conductor."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, Protocol

from doeff_conductor.effects.dsl import (
    AgentCall,
    GateCall,
    MergeCall,
    RandomCall,
    TimeCall,
    WorkspaceCall,
)


class WorkflowExpansionError(ValueError):
    """Raised when a workflow fails an expansion-time contract."""


@dataclass(frozen=True)
class Ref:
    name: str


@dataclass(frozen=True)
class FieldRef:
    source: Any
    field_name: str


@dataclass(frozen=True)
class OksProjection:
    source: Any


@dataclass(frozen=True)
class PromptExpr:
    parts: tuple[Any, ...]


def _next_workspace_occurrence() -> int:
    """Source-order occurrence index for ``workspace!`` expressions.

    Workspace identity belongs to the workspace EXPRESSION, not to the
    site that happens to evaluate it: a module-level ``(setv ws
    (workspace! ...))`` shared by several nodes is ONE workspace.  Keying
    identity by evaluation path instead silently gave every consumer its
    own fresh worktree — a gate would then test a different tree than the
    implementer wrote to (the same false-positive class the resume fix
    exists to kill).  The loader resets this counter before executing a
    workflow module, so identical source yields identical occurrence
    numbers across processes — the property resume stability needs.
    """
    global _WORKSPACE_OCCURRENCE  # noqa: PLW0603 - module-level counter is the existing state model
    value = _WORKSPACE_OCCURRENCE
    _WORKSPACE_OCCURRENCE += 1
    return value


_WORKSPACE_OCCURRENCE: int = 0


def reset_workspace_occurrences() -> None:
    """Reset the occurrence counter; called by the loader per module exec."""
    global _WORKSPACE_OCCURRENCE  # noqa: PLW0603 - module-level counter is the existing state model
    _WORKSPACE_OCCURRENCE = 0


@dataclass(frozen=True)
class WorkspaceSpec:
    repo: str | None = None
    from_ref: Any | None = None
    occurrence: int = dataclass_field(default_factory=_next_workspace_occurrence)

    def to_effect(self) -> WorkspaceCall:
        return WorkspaceCall(repo=self.repo, from_ref=self.from_ref)


@dataclass(frozen=True)
class MergeSpec:
    workspaces: tuple[Any, ...]
    strategy: str = "merge"
    budget: Any | None = None
    phase: str | None = None

    def to_effect(self) -> MergeCall:
        return MergeCall(workspaces=self.workspaces, strategy=self.strategy)


@dataclass(frozen=True)
class AgentSpec:
    role: str | None
    verification_class: str | None
    prompt: Any
    schema: Any
    workspace: Any | None = None
    files: frozenset[str] | None = None
    profile: str | None = None
    persona: str | None = None
    retry: int | None = None
    budget: Any | None = None
    deadline_seconds: Any | None = None
    label: str | None = None
    phase: str | None = None

    def to_effect(self, phase: str | None) -> AgentCall:
        if self.role is None:
            raise WorkflowExpansionError("agent! requires :role")
        if self.verification_class is None:
            raise WorkflowExpansionError("agent! requires explicit :class")
        if self.schema is None:
            raise WorkflowExpansionError("agent! requires :schema")
        return AgentCall(
            role=self.role,
            verification_class=self.verification_class,
            prompt=self.prompt,
            schema=self.schema,
            workspace=self.workspace,
            files=self.files or frozenset(),
            profile=self.profile,
            persona=self.persona,
            retry=self.retry,
            deadline_seconds=_parse_deadline_annotation(
                self.deadline_seconds, "agent! deadline-seconds"
            ),
            label=self.label,
            phase=self.phase or phase,
        )


@dataclass(frozen=True)
class GateSpec:
    cmd: str
    workspace: Any | None = None
    timeout: int | None = None
    budget: Any | None = None
    phase: str | None = None

    def to_effect(self, phase: str | None) -> GateCall:
        return GateCall(
            cmd=self.cmd,
            workspace=self.workspace,
            timeout=self.timeout,
            phase=self.phase or phase,
        )


@dataclass(frozen=True)
class TimeSpec:
    label: str | None = None
    budget: Any | None = None

    def to_effect(self) -> TimeCall:
        return TimeCall(label=self.label)


@dataclass(frozen=True)
class RandomSpec:
    spec: Any
    label: str | None = None
    budget: Any | None = None

    def to_effect(self) -> RandomCall:
        return RandomCall(spec=self.spec, label=self.label)


@dataclass(frozen=True)
class ParallelSpec:
    branches: tuple[Any, ...]
    quorum: int | None = None
    budget: Any | None = None


@dataclass(frozen=True)
class ParallelForSpec:
    var_name: str
    values: tuple[Any, ...]
    branches: tuple[Any, ...]
    budget: Any | None = None


@dataclass(frozen=True)
class LoopSpec:
    max_iterations: int
    until: Any
    body: tuple[Any, ...]
    budget: Any | None = None


@dataclass(frozen=True)
class BindSpec:
    target: str | tuple[str, ...]
    expr: Any


@dataclass(frozen=True)
class PhaseSpec:
    name: str
    stakes: str = "normal"
    body: tuple[Any, ...] = ()


@dataclass(frozen=True)
class ArtifactSpec:
    value: Any


@dataclass(frozen=True)
class ForbiddenSpec:
    name: str
    reason: str


@dataclass(frozen=True)
class RawNodeSpec:
    description: str


@dataclass
class BindingInfo:
    name: str
    node_id: str
    source_kind: str
    is_try: bool = False
    phase: str | None = None
    consumed: bool = False


@dataclass(frozen=True)
class ExpandedNode:
    node_id: str
    kind: str
    phase: str | None
    dependencies: tuple[str, ...] = ()
    effect: Any | None = None
    budget_units: int = 0


@dataclass(frozen=True)
class ExpandedWorkflow:
    name: str
    params: Mapping[str, Any]
    roles: Mapping[str, Mapping[str, Any]]
    phases: Mapping[str, PhaseSpec]
    nodes: tuple[ExpandedNode, ...]
    bindings: Mapping[str, BindingInfo]
    budget_total: int

    def node_ids(self) -> tuple[str, ...]:
        return tuple(node.node_id for node in self.nodes)


@dataclass
class _ExpressionInfo:
    node_id: str
    kind: str
    result_count: int = 1
    is_try: bool = False
    branch_node_ids: tuple[str, ...] = ()


@dataclass
class _ExpansionState:
    workflow_name: str
    roles: Mapping[str, Mapping[str, Any]]
    phases: Mapping[str, PhaseSpec]
    workflow_budget_limit: int | None
    nodes: list[ExpandedNode] = dataclass_field(default_factory=list)
    bindings: dict[str, BindingInfo] = dataclass_field(default_factory=dict)
    budget_total: int = 0
    required_merge_groups: list[frozenset[str]] = dataclass_field(default_factory=list)
    merged_workspace_keys: set[str] = dataclass_field(default_factory=set)

    def add_budget(self, budget: Any | None, context: str) -> int:
        units = _parse_budget_annotation(budget, context)
        self.budget_total += units
        if self.workflow_budget_limit is not None and self.budget_total > self.workflow_budget_limit:
            raise WorkflowExpansionError(
                f"budget annotations sum to {self.budget_total}, above workflow limit "
                f"{self.workflow_budget_limit}"
            )
        return units


class _ExpandExprHandler(Protocol):
    def __call__(
        self,
        expr: Any,
        state: _ExpansionState,
        *,
        current_phase: str | None,
        path: str,
    ) -> _ExpressionInfo: ...


@dataclass(frozen=True)
class WorkflowSpec:
    name: str
    params: Mapping[str, Any]
    roles: Mapping[str, Mapping[str, Any]]
    body: tuple[Any, ...]
    budget: Any | None = None

    def expand(self) -> ExpandedWorkflow:
        phases = _collect_phases(self.body)
        budget_limit = None
        if self.budget is not None:
            budget_limit = _parse_budget_annotation(self.budget, "workflow budget")
        state = _ExpansionState(
            workflow_name=self.name,
            roles=self.roles,
            phases=phases,
            workflow_budget_limit=budget_limit,
        )
        # :params entries are launch-time inputs: referenceable like bindings,
        # but pre-consumed because an unused param is not a closure violation.
        for param_name in self.params:
            state.bindings[param_name] = BindingInfo(
                name=param_name,
                node_id=f"param:{param_name}",
                source_kind="param",
                consumed=True,
            )
        _expand_forms(self.body, state, current_phase=None, path_prefix="")
        _validate_required_merges(state)
        _validate_unconsumed_bindings(state)
        return ExpandedWorkflow(
            name=self.name,
            params=self.params,
            roles=self.roles,
            phases=phases,
            nodes=tuple(state.nodes),
            bindings=dict(state.bindings),
            budget_total=state.budget_total,
        )


def defworkflow(
    name: str,
    *,
    params: Mapping[str, Any],
    roles: Mapping[str, Mapping[str, Any]],
    body: Sequence[Any],
    budget: Any | None = None,
) -> WorkflowSpec:
    normalized_roles = {
        _normalize_name(role_name): dict(role_spec)
        for role_name, role_spec in roles.items()
    }
    return WorkflowSpec(
        name=_normalize_name(name),
        params=dict(params),
        roles=normalized_roles,
        body=tuple(body),
        budget=budget,
    )


def defphase(name: str, *, stakes: str = "normal", body: Sequence[Any] | None = None) -> PhaseSpec:
    return PhaseSpec(name=_normalize_name(name), stakes=_normalize_name(stakes), body=tuple(body or ()))


def agent_bang(
    *,
    role: str | None = None,
    verification_class: str | None = None,
    prompt: Any = None,
    schema: Any = None,
    workspace: Any | None = None,
    files: Iterable[str] | None = None,
    profile: str | None = None,
    persona: str | None = None,
    retry: int | None = None,
    budget: Any | None = None,
    deadline_seconds: Any | None = None,
    label: str | None = None,
    phase: str | None = None,
    class_: str | None = None,
    **extra: Any,
) -> AgentSpec:
    kwargs = _normalize_hy_kwargs(extra)
    role = _pop_kw(kwargs, "role", role)
    verification_class = _pop_kw(kwargs, "verification_class", verification_class)
    prompt = _pop_kw(kwargs, "prompt", prompt)
    schema = _pop_kw(kwargs, "schema", schema)
    workspace = _pop_kw(kwargs, "workspace", workspace)
    files = _pop_kw(kwargs, "files", files)
    profile = _pop_kw(kwargs, "profile", profile)
    persona = _pop_kw(kwargs, "persona", persona)
    retry = _pop_kw(kwargs, "retry", retry)
    budget = _pop_kw(kwargs, "budget", budget)
    deadline_seconds = _pop_kw(kwargs, "deadline_seconds", deadline_seconds)
    label = _pop_kw(kwargs, "label", label)
    phase = _pop_kw(kwargs, "phase", phase)
    class_ = _pop_kw(kwargs, "class_", class_)
    _reject_unknown_kwargs(kwargs, "agent!")
    resolved_class = verification_class
    if resolved_class is None:
        resolved_class = class_
    return AgentSpec(
        role=_normalize_name(role) if role is not None else None,
        verification_class=_normalize_name(resolved_class) if resolved_class is not None else None,
        prompt=prompt,
        schema=schema,
        workspace=workspace,
        files=frozenset(files) if files is not None else None,
        profile=_normalize_name(profile) if profile is not None else None,
        persona=_normalize_name(persona) if persona is not None else None,
        retry=retry,
        budget=budget,
        deadline_seconds=deadline_seconds,
        label=label,
        phase=phase,
    )


def gate_bang(
    *,
    cmd: str | None = None,
    workspace: Any | None = None,
    timeout: int | None = None,
    budget: Any | None = None,
    phase: str | None = None,
    **extra: Any,
) -> GateSpec:
    kwargs = _normalize_hy_kwargs(extra)
    cmd = _pop_kw(kwargs, "cmd", cmd)
    workspace = _pop_kw(kwargs, "workspace", workspace)
    timeout = _pop_kw(kwargs, "timeout", timeout)
    budget = _pop_kw(kwargs, "budget", budget)
    phase = _pop_kw(kwargs, "phase", phase)
    _reject_unknown_kwargs(kwargs, "gate!")
    if cmd is None:
        raise WorkflowExpansionError("gate! requires :cmd")
    return GateSpec(cmd=cmd, workspace=workspace, timeout=timeout, budget=budget, phase=phase)


def workspace_bang(
    *,
    repo: str | None = None,
    from_: Any | None = None,
    **extra: Any,
) -> WorkspaceSpec:
    kwargs = _normalize_hy_kwargs(extra)
    repo = _pop_kw(kwargs, "repo", repo)
    from_ = _pop_kw(kwargs, "from_", from_)
    _reject_unknown_kwargs(kwargs, "workspace!")
    return WorkspaceSpec(repo=repo, from_ref=from_)


def merge_bang(
    *,
    workspaces: Sequence[Any] | None = None,
    strategy: str = "merge",
    budget: Any | None = None,
    phase: str | None = None,
    **extra: Any,
) -> MergeSpec:
    kwargs = _normalize_hy_kwargs(extra)
    workspaces = _pop_kw(kwargs, "workspaces", workspaces)
    strategy = _pop_kw(kwargs, "strategy", strategy)
    budget = _pop_kw(kwargs, "budget", budget)
    phase = _pop_kw(kwargs, "phase", phase)
    _reject_unknown_kwargs(kwargs, "merge!")
    if workspaces is None:
        raise WorkflowExpansionError("merge! requires :workspaces")
    return MergeSpec(workspaces=tuple(workspaces), strategy=strategy, budget=budget, phase=phase)


def time_bang(
    *,
    label: str | None = None,
    budget: Any | None = None,
    **extra: Any,
) -> TimeSpec:
    kwargs = _normalize_hy_kwargs(extra)
    label = _pop_kw(kwargs, "label", label)
    budget = _pop_kw(kwargs, "budget", budget)
    _reject_unknown_kwargs(kwargs, "time!")
    return TimeSpec(label=label, budget=budget)


def random_bang(
    spec: Any = None,
    *,
    label: str | None = None,
    budget: Any | None = None,
    **extra: Any,
) -> RandomSpec:
    kwargs = _normalize_hy_kwargs(extra)
    spec = _pop_kw(kwargs, "spec", spec)
    label = _pop_kw(kwargs, "label", label)
    budget = _pop_kw(kwargs, "budget", budget)
    _reject_unknown_kwargs(kwargs, "random!")
    return RandomSpec(spec=spec, label=label, budget=budget)


def parallel(
    *branches: Any,
    quorum: int | None = None,
    budget: Any | None = None,
    **extra: Any,
) -> ParallelSpec:
    kwargs = _normalize_hy_kwargs(extra)
    quorum = _pop_kw(kwargs, "quorum", quorum)
    budget = _pop_kw(kwargs, "budget", budget)
    _reject_unknown_kwargs(kwargs, "parallel")
    return ParallelSpec(branches=tuple(branches), quorum=quorum, budget=budget)


def parallel_for(
    var_name: str,
    values: Sequence[Any],
    body: Callable[[Any], Any],
    *,
    budget: Any | None = None,
    **extra: Any,
) -> ParallelForSpec:
    kwargs = _normalize_hy_kwargs(extra)
    budget = _pop_kw(kwargs, "budget", budget)
    _reject_unknown_kwargs(kwargs, "parallel-for")
    if isinstance(values, (str, bytes)):
        raise WorkflowExpansionError("parallel-for requires a literal sequence, not a string")
    branches = tuple(body(value) for value in values)
    return ParallelForSpec(
        var_name=_normalize_name(var_name),
        values=tuple(values),
        branches=branches,
        budget=budget,
    )


def loop(
    *,
    max_iterations: int | None = None,
    until: Any | None = None,
    body: Sequence[Any] | None = None,
    budget: Any | None = None,
    **extra: Any,
) -> LoopSpec:
    kwargs = _normalize_hy_kwargs(extra)
    max_iterations = _pop_kw(kwargs, "max", max_iterations)
    until = _pop_kw(kwargs, "until", until)
    body = _pop_kw(kwargs, "body", body)
    budget = _pop_kw(kwargs, "budget", budget)
    _reject_unknown_kwargs(kwargs, "loop")
    if max_iterations is None:
        raise WorkflowExpansionError("loop requires :max")
    if until is None:
        raise WorkflowExpansionError("loop requires :until")
    if body is None:
        raise WorkflowExpansionError("loop requires a body")
    return LoopSpec(max_iterations=max_iterations, until=until, body=tuple(body), budget=budget)


def bind(target: str | Sequence[str], expr: Any) -> BindSpec:
    if isinstance(target, str):
        parsed_target: str | tuple[str, ...] = target
    else:
        parsed_target = tuple(str(item) for item in target)
    return BindSpec(target=parsed_target, expr=expr)


def artifact(value: Any) -> ArtifactSpec:
    return ArtifactSpec(value=value)


def ref(name: str) -> Ref:
    return Ref(name=name)


def field(source: Any, field_name: str) -> FieldRef:
    return FieldRef(source=source, field_name=field_name)


def deref(source: Any, field_name: str) -> FieldRef:
    return field(source, field_name)


def oks(source: Any) -> OksProjection:
    return OksProjection(source=source)


def prompt(*parts: Any) -> PromptExpr:
    return PromptExpr(parts=tuple(parts))


def pipeline(*forms: Any) -> ForbiddenSpec:
    _ = forms
    return ForbiddenSpec(
        name="pipeline",
        reason="pipeline is OPEN for v1; use static parallel or parallel-for",
    )


def ask(name: str) -> ForbiddenSpec:
    return ForbiddenSpec(
        name="ask",
        reason=f"binding locality violation: ask({name!r}) is not in the closed DSL",
    )


def local(bindings: Mapping[str, Any], body: Any) -> ForbiddenSpec:
    _ = bindings
    _ = body
    return ForbiddenSpec(
        name="Local",
        reason="binding locality violation: Local/dynamic scoping is not in the closed DSL",
    )


def raw_node(description: str) -> RawNodeSpec:
    return RawNodeSpec(description=description)


agent = agent_bang
gate = gate_bang
workspace = workspace_bang
merge = merge_bang
time = time_bang
random_value = random_bang


def _collect_phases(body: Sequence[Any]) -> dict[str, PhaseSpec]:
    phases: dict[str, PhaseSpec] = {}
    for form in body:
        if isinstance(form, PhaseSpec):
            if not form.name:
                raise WorkflowExpansionError("phase declarations require a name")
            if form.name in phases:
                raise WorkflowExpansionError(f"duplicate phase declaration: {form.name}")
            if form.stakes not in {"low", "normal", "high"}:
                raise WorkflowExpansionError(
                    f"phase {form.name} has invalid stakes {form.stakes!r}"
                )
            if not form.body:
                raise WorkflowExpansionError(f"phase {form.name} is orphaned: no nodes")
            phases[form.name] = form
    return phases


def _expand_forms(
    forms: Sequence[Any],
    state: _ExpansionState,
    *,
    current_phase: str | None,
    path_prefix: str,
) -> None:
    for index, form in enumerate(forms):
        path = _path_join(path_prefix, str(index))
        _expand_form(form, state, current_phase=current_phase, path=path)


def _expand_form(
    form: Any,
    state: _ExpansionState,
    *,
    current_phase: str | None,
    path: str,
) -> _ExpressionInfo | None:
    if isinstance(form, ForbiddenSpec):
        raise WorkflowExpansionError(form.reason)
    if isinstance(form, RawNodeSpec):
        raise WorkflowExpansionError(
            f"closure law violation: raw node {form.description!r} has no artifact, verdict, "
            "escalation, or gate terminal"
        )
    if isinstance(form, PhaseSpec):
        _expand_forms(
            form.body,
            state,
            current_phase=form.name,
            path_prefix=_path_join(path, form.name),
        )
        return None
    if isinstance(form, BindSpec):
        return _expand_bind(form, state, current_phase=current_phase, path=path)
    if isinstance(form, ArtifactSpec):
        dependencies = _validate_expr_refs(form.value, state, allow_try_ref=False)
        node_id = _node_id(state.workflow_name, path, "artifact")
        state.nodes.append(
            ExpandedNode(
                node_id=node_id,
                kind="artifact",
                phase=current_phase,
                dependencies=tuple(sorted(dependencies)),
            )
        )
        return _ExpressionInfo(node_id=node_id, kind="artifact")
    return _expand_expr(form, state, current_phase=current_phase, path=path)


def _expand_bind(
    form: BindSpec,
    state: _ExpansionState,
    *,
    current_phase: str | None,
    path: str,
) -> _ExpressionInfo:
    info = _expand_expr(form.expr, state, current_phase=current_phase, path=path)
    if isinstance(form.target, tuple):
        if info.result_count != len(form.target):
            raise WorkflowExpansionError(
                f"parallel join for {form.target!r} expected {len(form.target)} branches, "
                f"got {info.result_count}"
            )
        if info.is_try:
            raise WorkflowExpansionError("quorum parallel joins must bind to one Try-typed aggregate")
        for index, name in enumerate(form.target):
            _bind_name(
                name,
                node_id=f"{info.node_id}[{index}]",
                source_kind=info.kind,
                is_try=False,
                phase=current_phase,
                state=state,
            )
        return info
    _bind_name(
        form.target,
        node_id=info.node_id,
        source_kind=info.kind,
        is_try=info.is_try,
        phase=current_phase,
        state=state,
    )
    return info


def _expand_expr(
    expr: Any,
    state: _ExpansionState,
    *,
    current_phase: str | None,
    path: str,
) -> _ExpressionInfo:
    if isinstance(expr, ForbiddenSpec):
        raise WorkflowExpansionError(expr.reason)
    if isinstance(expr, RawNodeSpec):
        raise WorkflowExpansionError(
            f"closure law violation: raw node {expr.description!r} has no closure terminal"
        )
    info: _ExpressionInfo | None = None
    for expr_type, handler in _EXPAND_EXPR_HANDLERS:
        if isinstance(expr, expr_type):
            info = handler(expr, state, current_phase=current_phase, path=path)
            break
    if info is None:
        raise WorkflowExpansionError(
            f"closure law violation: unsupported DSL form {type(expr).__name__}"
        )
    return info


def _expand_parallel_expr(
    expr: Any,
    state: _ExpansionState,
    *,
    current_phase: str | None,
    path: str,
) -> _ExpressionInfo:
    if not isinstance(expr, ParallelSpec):
        raise TypeError("_expand_parallel_expr requires ParallelSpec")
    return _expand_parallel(
        expr.branches,
        expr.quorum,
        expr.budget,
        state,
        current_phase=current_phase,
        path=path,
        fanout_label="parallel",
    )


def _expand_agent_expr(
    expr: Any,
    state: _ExpansionState,
    *,
    current_phase: str | None,
    path: str,
) -> _ExpressionInfo:
    if not isinstance(expr, AgentSpec):
        raise TypeError("_expand_agent_expr requires AgentSpec")
    return _expand_agent(expr, state, current_phase=current_phase, path=path)


def _expand_gate_expr(
    expr: Any,
    state: _ExpansionState,
    *,
    current_phase: str | None,
    path: str,
) -> _ExpressionInfo:
    if not isinstance(expr, GateSpec):
        raise TypeError("_expand_gate_expr requires GateSpec")
    return _expand_gate(expr, state, current_phase=current_phase, path=path)


def _expand_merge_expr(
    expr: Any,
    state: _ExpansionState,
    *,
    current_phase: str | None,
    path: str,
) -> _ExpressionInfo:
    if not isinstance(expr, MergeSpec):
        raise TypeError("_expand_merge_expr requires MergeSpec")
    return _expand_merge(expr, state, current_phase=current_phase, path=path)


def _expand_time_expr(
    expr: Any,
    state: _ExpansionState,
    *,
    current_phase: str | None,
    path: str,
) -> _ExpressionInfo:
    if not isinstance(expr, TimeSpec):
        raise TypeError("_expand_time_expr requires TimeSpec")
    return _expand_time(expr, state, current_phase=current_phase, path=path)


def _expand_random_expr(
    expr: Any,
    state: _ExpansionState,
    *,
    current_phase: str | None,
    path: str,
) -> _ExpressionInfo:
    if not isinstance(expr, RandomSpec):
        raise TypeError("_expand_random_expr requires RandomSpec")
    return _expand_random(expr, state, current_phase=current_phase, path=path)


def _expand_workspace_expr(
    expr: Any,
    state: _ExpansionState,
    *,
    current_phase: str | None,
    path: str,
) -> _ExpressionInfo:
    if not isinstance(expr, WorkspaceSpec):
        raise TypeError("_expand_workspace_expr requires WorkspaceSpec")
    return _expand_workspace(expr, state, current_phase=current_phase, path=path)


def _expand_parallel_for_expr(
    expr: Any,
    state: _ExpansionState,
    *,
    current_phase: str | None,
    path: str,
) -> _ExpressionInfo:
    if not isinstance(expr, ParallelForSpec):
        raise TypeError("_expand_parallel_for_expr requires ParallelForSpec")
    return _expand_parallel(
        expr.branches,
        None,
        expr.budget,
        state,
        current_phase=current_phase,
        path=path,
        fanout_label="parallel-for",
    )


def _expand_loop_expr(
    expr: Any,
    state: _ExpansionState,
    *,
    current_phase: str | None,
    path: str,
) -> _ExpressionInfo:
    if not isinstance(expr, LoopSpec):
        raise TypeError("_expand_loop_expr requires LoopSpec")
    return _expand_loop(expr, state, current_phase=current_phase, path=path)


def _expand_glue_expr(
    expr: Any,
    state: _ExpansionState,
    *,
    current_phase: str | None,
    path: str,
) -> _ExpressionInfo:
    dependencies = _validate_expr_refs(expr, state, allow_try_ref=False)
    node_id = _node_id(state.workflow_name, path, "glue")
    state.nodes.append(
        ExpandedNode(
            node_id=node_id,
            kind="glue",
            phase=current_phase,
            dependencies=tuple(sorted(dependencies)),
        )
    )
    return _ExpressionInfo(node_id=node_id, kind="glue")


def _expand_agent(
    spec: AgentSpec,
    state: _ExpansionState,
    *,
    current_phase: str | None,
    path: str,
) -> _ExpressionInfo:
    effective_phase = spec.phase or current_phase
    _validate_phase_use(effective_phase, state)
    if spec.schema is None:
        raise WorkflowExpansionError("every agent! must carry :schema")
    if spec.verification_class is None:
        raise WorkflowExpansionError("every agent! must carry an explicit :class")
    if spec.role is None:
        raise WorkflowExpansionError("agent! requires :role")
    if spec.role not in state.roles:
        raise WorkflowExpansionError(f"agent! role {spec.role!r} does not resolve")
    dependencies = _validate_expr_refs(spec.prompt, state, allow_try_ref=False)
    dependencies.update(_validate_expr_refs(spec.workspace, state, allow_try_ref=False))
    budget_units = state.add_budget(spec.budget, "agent! budget")
    _parse_deadline_annotation(spec.deadline_seconds, "agent! deadline-seconds")
    node_id = _node_id(state.workflow_name, path, "agent")
    state.nodes.append(
        ExpandedNode(
            node_id=node_id,
            kind="agent",
            phase=effective_phase,
            dependencies=tuple(sorted(dependencies)),
            effect=spec.to_effect(effective_phase),
            budget_units=budget_units,
        )
    )
    return _ExpressionInfo(node_id=node_id, kind="agent")


def _expand_gate(
    spec: GateSpec,
    state: _ExpansionState,
    *,
    current_phase: str | None,
    path: str,
) -> _ExpressionInfo:
    effective_phase = spec.phase or current_phase
    _validate_phase_use(effective_phase, state)
    dependencies = _validate_expr_refs(spec.workspace, state, allow_try_ref=False)
    budget_units = state.add_budget(spec.budget, "gate! budget")
    node_id = _node_id(state.workflow_name, path, "gate")
    state.nodes.append(
        ExpandedNode(
            node_id=node_id,
            kind="gate",
            phase=effective_phase,
            dependencies=tuple(sorted(dependencies)),
            effect=spec.to_effect(effective_phase),
            budget_units=budget_units,
        )
    )
    return _ExpressionInfo(node_id=node_id, kind="gate")


def _expand_merge(
    spec: MergeSpec,
    state: _ExpansionState,
    *,
    current_phase: str | None,
    path: str,
) -> _ExpressionInfo:
    effective_phase = spec.phase or current_phase
    _validate_phase_use(effective_phase, state)
    dependencies: set[str] = set()
    for workspace_value in spec.workspaces:
        dependencies.update(_validate_expr_refs(workspace_value, state, allow_try_ref=False))
        workspace_key = _workspace_key(workspace_value)
        if workspace_key is not None:
            state.merged_workspace_keys.add(workspace_key)
    budget_units = state.add_budget(spec.budget, "merge! budget")
    node_id = _node_id(state.workflow_name, path, "merge")
    state.nodes.append(
        ExpandedNode(
            node_id=node_id,
            kind="merge",
            phase=effective_phase,
            dependencies=tuple(sorted(dependencies)),
            effect=spec.to_effect(),
            budget_units=budget_units,
        )
    )
    return _ExpressionInfo(node_id=node_id, kind="merge")


def _expand_time(
    spec: TimeSpec,
    state: _ExpansionState,
    *,
    current_phase: str | None,
    path: str,
) -> _ExpressionInfo:
    budget_units = state.add_budget(spec.budget, "time! budget")
    node_id = _node_id(state.workflow_name, path, "time")
    state.nodes.append(
        ExpandedNode(
            node_id=node_id,
            kind="time",
            phase=current_phase,
            effect=spec.to_effect(),
            budget_units=budget_units,
        )
    )
    return _ExpressionInfo(node_id=node_id, kind="time")


def _expand_random(
    spec: RandomSpec,
    state: _ExpansionState,
    *,
    current_phase: str | None,
    path: str,
) -> _ExpressionInfo:
    dependencies = _validate_expr_refs(spec.spec, state, allow_try_ref=False)
    budget_units = state.add_budget(spec.budget, "random! budget")
    node_id = _node_id(state.workflow_name, path, "random")
    state.nodes.append(
        ExpandedNode(
            node_id=node_id,
            kind="random",
            phase=current_phase,
            dependencies=tuple(sorted(dependencies)),
            effect=spec.to_effect(),
            budget_units=budget_units,
        )
    )
    return _ExpressionInfo(node_id=node_id, kind="random")


def _expand_workspace(
    spec: WorkspaceSpec,
    state: _ExpansionState,
    *,
    current_phase: str | None,
    path: str,
) -> _ExpressionInfo:
    dependencies = _validate_expr_refs(spec.from_ref, state, allow_try_ref=False)
    node_id = _node_id(state.workflow_name, path, "workspace")
    state.nodes.append(
        ExpandedNode(
            node_id=node_id,
            kind="workspace",
            phase=current_phase,
            dependencies=tuple(sorted(dependencies)),
            effect=spec.to_effect(),
        )
    )
    return _ExpressionInfo(node_id=node_id, kind="workspace")


def _expand_parallel(
    branches: tuple[Any, ...],
    quorum: int | None,
    budget: Any | None,
    state: _ExpansionState,
    *,
    current_phase: str | None,
    path: str,
    fanout_label: str,
) -> _ExpressionInfo:
    if not branches:
        raise WorkflowExpansionError(f"{fanout_label} joins must have at least one branch")
    branch_count = len(branches)
    resolved_quorum = branch_count if quorum is None else quorum
    if not isinstance(resolved_quorum, int) or isinstance(resolved_quorum, bool):
        raise WorkflowExpansionError(f"{fanout_label} quorum must be an integer")
    if resolved_quorum < 1 or resolved_quorum > branch_count:
        raise WorkflowExpansionError(
            f"{fanout_label} quorum {resolved_quorum} is outside 1..{branch_count}"
        )
    _validate_parallel_workspace_writes(branches, state)
    budget_units = state.add_budget(budget, f"{fanout_label} budget")
    branch_node_ids: list[str] = []
    for branch_index, branch in enumerate(branches):
        branch_path = _path_join(path, f"{fanout_label}[{branch_index}]")
        branch_info = _expand_expr(
            branch,
            state,
            current_phase=current_phase,
            path=branch_path,
        )
        branch_node_ids.append(branch_info.node_id)
    node_id = _node_id(state.workflow_name, path, fanout_label)
    state.nodes.append(
        ExpandedNode(
            node_id=node_id,
            kind=fanout_label,
            phase=current_phase,
            dependencies=tuple(branch_node_ids),
            budget_units=budget_units,
        )
    )
    return _ExpressionInfo(
        node_id=node_id,
        kind=fanout_label,
        result_count=branch_count,
        is_try=resolved_quorum < branch_count,
        branch_node_ids=tuple(branch_node_ids),
    )


def _expand_loop(
    spec: LoopSpec,
    state: _ExpansionState,
    *,
    current_phase: str | None,
    path: str,
) -> _ExpressionInfo:
    if not isinstance(spec.max_iterations, int) or isinstance(spec.max_iterations, bool):
        raise WorkflowExpansionError("loop :max must be an integer")
    if spec.max_iterations < 1:
        raise WorkflowExpansionError("loop :max must be positive")
    dependencies = _validate_expr_refs(spec.until, state, allow_try_ref=False)
    budget_units = state.add_budget(spec.budget, "loop budget")
    before_names = set(state.bindings)
    body_node_start = len(state.nodes)
    _expand_forms(
        spec.body,
        state,
        current_phase=current_phase,
        path_prefix=_path_join(path, "loop"),
    )
    local_names = set(state.bindings) - before_names
    _validate_local_bindings_consumed(state, local_names)
    for local_name in local_names:
        del state.bindings[local_name]
    body_node_ids = tuple(node.node_id for node in state.nodes[body_node_start:])
    node_id = _node_id(state.workflow_name, path, "loop")
    state.nodes.append(
        ExpandedNode(
            node_id=node_id,
            kind="loop",
            phase=current_phase,
            dependencies=tuple(sorted(set(body_node_ids) | dependencies)),
            budget_units=budget_units,
        )
    )
    return _ExpressionInfo(node_id=node_id, kind="loop")


_EXPAND_EXPR_HANDLERS: tuple[
    tuple[type[Any] | tuple[type[Any], ...], _ExpandExprHandler],
    ...,
] = (
    (AgentSpec, _expand_agent_expr),
    (GateSpec, _expand_gate_expr),
    (MergeSpec, _expand_merge_expr),
    (TimeSpec, _expand_time_expr),
    (RandomSpec, _expand_random_expr),
    (WorkspaceSpec, _expand_workspace_expr),
    (ParallelSpec, _expand_parallel_expr),
    (ParallelForSpec, _expand_parallel_for_expr),
    (LoopSpec, _expand_loop_expr),
    ((Ref, FieldRef, OksProjection, PromptExpr), _expand_glue_expr),
)


def _bind_name(
    name: str,
    *,
    node_id: str,
    source_kind: str,
    is_try: bool,
    phase: str | None,
    state: _ExpansionState,
) -> None:
    if not name:
        raise WorkflowExpansionError("binding target name cannot be empty")
    if name in state.bindings:
        raise WorkflowExpansionError(f"binding locality violation: duplicate binding {name!r}")
    state.bindings[name] = BindingInfo(
        name=name,
        node_id=node_id,
        source_kind=source_kind,
        is_try=is_try,
        phase=phase,
    )


def _validate_expr_refs(expr: Any, state: _ExpansionState, *, allow_try_ref: bool) -> set[str]:
    dependencies: set[str] = set()
    if expr is not None:
        _collect_expr_refs(expr, state, allow_try_ref=allow_try_ref, dependencies=dependencies)
    return dependencies


def _collect_expr_refs(
    expr: Any,
    state: _ExpansionState,
    *,
    allow_try_ref: bool,
    dependencies: set[str],
) -> None:
    for expr_type, collector in _EXPR_REF_COLLECTORS:
        if isinstance(expr, expr_type):
            collector(expr, state, allow_try_ref=allow_try_ref, dependencies=dependencies)
            return


def _collect_ref_expr(
    expr: Any,
    state: _ExpansionState,
    *,
    allow_try_ref: bool,
    dependencies: set[str],
) -> None:
    if not isinstance(expr, Ref):
        raise TypeError("_collect_ref_expr requires Ref")
    if expr.name not in state.bindings:
        raise WorkflowExpansionError(f"undefined reference {expr.name!r}")
    binding = state.bindings[expr.name]
    if binding.is_try and not allow_try_ref:
        raise WorkflowExpansionError(
            f"Try-typed binding {expr.name!r} must be handled explicitly before use"
        )
    binding.consumed = True
    dependencies.add(binding.node_id)


def _collect_field_ref_expr(
    expr: Any,
    state: _ExpansionState,
    *,
    allow_try_ref: bool,
    dependencies: set[str],
) -> None:
    if not isinstance(expr, FieldRef):
        raise TypeError("_collect_field_ref_expr requires FieldRef")
    if isinstance(expr.source, Ref):
        source_name = expr.source.name
        if source_name not in state.bindings:
            raise WorkflowExpansionError(f"undefined reference {source_name!r}")
        if state.bindings[source_name].is_try:
            raise WorkflowExpansionError(
                f"Try-typed binding {source_name!r} cannot be dereferenced directly"
            )
    dependencies.update(_validate_expr_refs(expr.source, state, allow_try_ref=allow_try_ref))


def _collect_oks_expr(
    expr: Any,
    state: _ExpansionState,
    *,
    allow_try_ref: bool,
    dependencies: set[str],
) -> None:
    if not isinstance(expr, OksProjection):
        raise TypeError("_collect_oks_expr requires OksProjection")
    _ = allow_try_ref
    dependencies.update(_validate_expr_refs(expr.source, state, allow_try_ref=True))


def _collect_prompt_expr(
    expr: Any,
    state: _ExpansionState,
    *,
    allow_try_ref: bool,
    dependencies: set[str],
) -> None:
    if not isinstance(expr, PromptExpr):
        raise TypeError("_collect_prompt_expr requires PromptExpr")
    for part in expr.parts:
        dependencies.update(_validate_expr_refs(part, state, allow_try_ref=allow_try_ref))


def _collect_workspace_expr(
    expr: Any,
    state: _ExpansionState,
    *,
    allow_try_ref: bool,
    dependencies: set[str],
) -> None:
    if not isinstance(expr, WorkspaceSpec):
        raise TypeError("_collect_workspace_expr requires WorkspaceSpec")
    dependencies.update(_validate_expr_refs(expr.from_ref, state, allow_try_ref=allow_try_ref))


def _collect_merge_expr(
    expr: Any,
    state: _ExpansionState,
    *,
    allow_try_ref: bool,
    dependencies: set[str],
) -> None:
    if not isinstance(expr, MergeSpec):
        raise TypeError("_collect_merge_expr requires MergeSpec")
    for workspace_value in expr.workspaces:
        dependencies.update(_validate_expr_refs(workspace_value, state, allow_try_ref=allow_try_ref))


def _collect_agent_expr(
    expr: Any,
    state: _ExpansionState,
    *,
    allow_try_ref: bool,
    dependencies: set[str],
) -> None:
    if not isinstance(expr, AgentSpec):
        raise TypeError("_collect_agent_expr requires AgentSpec")
    dependencies.update(_validate_expr_refs(expr.prompt, state, allow_try_ref=allow_try_ref))
    dependencies.update(_validate_expr_refs(expr.workspace, state, allow_try_ref=allow_try_ref))


def _collect_gate_expr(
    expr: Any,
    state: _ExpansionState,
    *,
    allow_try_ref: bool,
    dependencies: set[str],
) -> None:
    if not isinstance(expr, GateSpec):
        raise TypeError("_collect_gate_expr requires GateSpec")
    dependencies.update(_validate_expr_refs(expr.workspace, state, allow_try_ref=allow_try_ref))


def _collect_random_expr(
    expr: Any,
    state: _ExpansionState,
    *,
    allow_try_ref: bool,
    dependencies: set[str],
) -> None:
    if not isinstance(expr, RandomSpec):
        raise TypeError("_collect_random_expr requires RandomSpec")
    dependencies.update(_validate_expr_refs(expr.spec, state, allow_try_ref=allow_try_ref))


def _collect_mapping_expr(
    expr: Any,
    state: _ExpansionState,
    *,
    allow_try_ref: bool,
    dependencies: set[str],
) -> None:
    if not isinstance(expr, Mapping):
        raise TypeError("_collect_mapping_expr requires Mapping")
    for value in expr.values():
        dependencies.update(_validate_expr_refs(value, state, allow_try_ref=allow_try_ref))


def _collect_iterable_expr(
    expr: Any,
    state: _ExpansionState,
    *,
    allow_try_ref: bool,
    dependencies: set[str],
) -> None:
    if not isinstance(expr, (list, tuple, set, frozenset)):
        raise TypeError("_collect_iterable_expr requires an iterable container")
    for item in expr:
        dependencies.update(_validate_expr_refs(item, state, allow_try_ref=allow_try_ref))


_EXPR_REF_COLLECTORS = (
    (Ref, _collect_ref_expr),
    (FieldRef, _collect_field_ref_expr),
    (OksProjection, _collect_oks_expr),
    (PromptExpr, _collect_prompt_expr),
    (WorkspaceSpec, _collect_workspace_expr),
    (MergeSpec, _collect_merge_expr),
    (AgentSpec, _collect_agent_expr),
    (GateSpec, _collect_gate_expr),
    (RandomSpec, _collect_random_expr),
    (Mapping, _collect_mapping_expr),
    ((list, tuple, set, frozenset), _collect_iterable_expr),
)


def _validate_phase_use(phase_name: str | None, state: _ExpansionState) -> None:
    if phase_name is not None and phase_name not in state.phases:
        raise WorkflowExpansionError(f"phase use {phase_name!r} has no matching declaration")


def _validate_parallel_workspace_writes(
    branches: tuple[Any, ...],
    state: _ExpansionState,
) -> None:
    agents = [branch for branch in branches if isinstance(branch, AgentSpec)]
    workspace_agents = [agent_spec for agent_spec in agents if agent_spec.workspace is not None]
    if len(workspace_agents) < 2:
        return
    by_workspace: dict[str, list[AgentSpec]] = {}
    for agent_spec in workspace_agents:
        workspace_key = _workspace_key(agent_spec.workspace)
        if workspace_key is None:
            continue
        by_workspace.setdefault(workspace_key, []).append(agent_spec)
    shared_workspace_found = False
    for workspace_key, agent_specs in by_workspace.items():
        if len(agent_specs) < 2:
            continue
        shared_workspace_found = True
        seen_files: set[str] = set()
        for agent_spec in agent_specs:
            if not agent_spec.files:
                raise WorkflowExpansionError(
                    f"parallel writers sharing workspace {workspace_key} must declare :files"
                )
            overlap = seen_files & set(agent_spec.files)
            if overlap:
                raise WorkflowExpansionError(
                    f"parallel writers sharing workspace {workspace_key} have overlapping files: "
                    f"{sorted(overlap)}"
                )
            seen_files.update(agent_spec.files)
    if not shared_workspace_found and len(by_workspace) > 1:
        state.required_merge_groups.append(frozenset(by_workspace))


def _validate_required_merges(state: _ExpansionState) -> None:
    for workspace_group in state.required_merge_groups:
        if not workspace_group.issubset(state.merged_workspace_keys):
            missing = sorted(workspace_group - state.merged_workspace_keys)
            raise WorkflowExpansionError(
                "parallel isolated workspaces require a downstream merge!; missing "
                f"{missing}"
            )


def _validate_unconsumed_bindings(state: _ExpansionState) -> None:
    _validate_local_bindings_consumed(state, set(state.bindings))


def _validate_local_bindings_consumed(state: _ExpansionState, names: set[str]) -> None:
    unconsumed = sorted(name for name in names if not state.bindings[name].consumed)
    if unconsumed:
        raise WorkflowExpansionError(f"unconsumed result bindings: {unconsumed}")


def _workspace_key(workspace_value: Any) -> str | None:
    if workspace_value is None:
        return None
    if isinstance(workspace_value, WorkspaceSpec):
        return f"workspace:{_stable_key(workspace_value.repo)}:{_stable_key(workspace_value.from_ref)}"
    if isinstance(workspace_value, Ref):
        return f"ref:{workspace_value.name}"
    return f"value:{_stable_key(workspace_value)}"


def _stable_key(value: Any) -> str:
    if isinstance(value, Ref):
        return f"ref:{value.name}"
    if isinstance(value, WorkspaceSpec):
        return _workspace_key(value) or "workspace:none"
    if isinstance(value, PromptExpr):
        return "prompt:" + "|".join(_stable_key(part) for part in value.parts)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_stable_key(item) for item in value) + "]"
    if isinstance(value, (set, frozenset)):
        return "{" + ",".join(sorted(_stable_key(item) for item in value)) + "}"
    return repr(value)


def _parse_budget_annotation(budget: Any | None, context: str) -> int:
    if budget is None:
        return 0
    if isinstance(budget, bool):
        raise WorkflowExpansionError(f"{context} must be numeric, got bool")
    if isinstance(budget, int):
        if budget < 0:
            raise WorkflowExpansionError(f"{context} must be non-negative")
        return budget
    if isinstance(budget, float):
        if budget < 0 or not budget.is_integer():
            raise WorkflowExpansionError(f"{context} must be a non-negative integer token count")
        return int(budget)
    if isinstance(budget, str):
        text = budget.strip().lower().replace("_", "")
        multiplier = 1
        if text.endswith("k"):
            multiplier = 1_000
            text = text[:-1]
        elif text.endswith("m"):
            multiplier = 1_000_000
            text = text[:-1]
        if not text.isdigit():
            raise WorkflowExpansionError(f"invalid budget annotation {budget!r}")
        return int(text) * multiplier
    raise WorkflowExpansionError(f"invalid budget annotation type {type(budget).__name__}")


def _parse_deadline_annotation(deadline_seconds: Any | None, context: str) -> float | None:
    """Validate the wall-clock deadline node-spec attribute (L-K4-3).

    The deadline is declared on the node spec (k8s ``activeDeadlineSeconds``
    semantics) and observed by the L3 runtime; expansion rejects malformed
    annotations so a bad deadline fails at plan/validate time, never mid-run.
    """
    if deadline_seconds is None:
        return None
    if isinstance(deadline_seconds, bool) or not isinstance(deadline_seconds, (int, float)):
        raise WorkflowExpansionError(
            f"{context} must be a positive number of seconds, "
            f"got {type(deadline_seconds).__name__}"
        )
    parsed = float(deadline_seconds)
    if not math.isfinite(parsed):
        raise WorkflowExpansionError(f"{context} must be finite, got {deadline_seconds!r}")
    if parsed <= 0:
        raise WorkflowExpansionError(
            f"{context} must be a positive number of seconds, got {deadline_seconds!r}"
        )
    return parsed


def _node_id(workflow_name: str, path: str, kind: str) -> str:
    if path:
        return f"{workflow_name}/{path}/{kind}"
    return f"{workflow_name}/{kind}"


def _path_join(prefix: str, child: str) -> str:
    if not prefix:
        return child
    return f"{prefix}/{child}"


def _normalize_name(value: Any) -> str:
    text = str(value)
    if text.startswith(":"):
        return text[1:]
    return text


def _normalize_hy_kwargs(extra: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for raw_key, value in extra.items():
        key = str(raw_key)
        if key.startswith("hyx_XcolonX"):
            key = key.removeprefix("hyx_XcolonX")
        if key.startswith(":"):
            key = key[1:]
        key = key.replace("XhyphenX", "_")
        key = key.replace("-", "_")
        if key == "class":
            key = "class_"
        if key == "from":
            key = "from_"
        if key in normalized:
            raise WorkflowExpansionError(f"duplicate keyword argument {key!r}")
        normalized[key] = value
    return normalized


def _pop_kw(kwargs: dict[str, Any], name: str, current: Any) -> Any:
    if name not in kwargs:
        return current
    value = kwargs[name]
    del kwargs[name]
    return value


def _reject_unknown_kwargs(kwargs: Mapping[str, Any], context: str) -> None:
    if kwargs:
        raise WorkflowExpansionError(f"{context} got unknown keyword arguments: {sorted(kwargs)}")
