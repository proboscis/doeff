"""Production interpreter for ``WorkflowSpec`` request artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from dataclasses import field as dataclass_field
from typing import Any, cast

from doeff import Gather, Spawn, do
from doeff_conductor.dsl import (
    AgentSpec,
    ArtifactSpec,
    BindSpec,
    FieldRef,
    GateSpec,
    LoopSpec,
    MergeSpec,
    OksProjection,
    ParallelForSpec,
    ParallelSpec,
    PhaseSpec,
    PromptExpr,
    RandomSpec,
    Ref,
    TimeSpec,
    WorkflowSpec,
    WorkspaceSpec,
)
from doeff_conductor.effects import (
    Agent,
    AgentAttemptExhaustedError,
    AgentDeadlineExceededError,
    AgentTask,
    Commit,
    CreateWorkspace,
    Exec,
    MergeWorkspaces,
    RandomCall,
    TimeCall,
)
from doeff_conductor.environment import (
    ProfileBinding,
    ProfileRegistry,
    load_profile_registry_from_env,
)
from doeff_conductor.overseer import GateOption, OpenGateView
from doeff_conductor.replay_keying import ResolvedIdentity
from doeff_conductor.types import (
    ExecResult,
    Issue,
    MergeConflict,
    MergeStrategy,
    MergeWorkspacesResult,
    Workspace,
)
from doeff_conductor.verbs import resolve_agent_profile


@dataclass
class _RuntimeContext:
    workflow: WorkflowSpec
    run_id: str
    params: Mapping[str, Any]
    registry: ProfileRegistry
    issue: Issue | None
    supervision: str
    answered_gate_options: Mapping[str, str]
    answered_retry_agent_counts: Mapping[str, int]
    answered_gate_stakes: Mapping[str, Mapping[str, Any]]
    bindings: dict[str, Any] = dataclass_field(default_factory=dict)
    open_gates: dict[str, OpenGateView] = dataclass_field(default_factory=dict)
    # Keyed by the workspace EXPRESSION's source-order occurrence, NOT by
    # Python object id and NOT by evaluation site — one workspace! value
    # shared by several nodes must bind one workspace, identically across
    # process restarts (resume stability).
    workspace_cache: dict[str, Workspace] = dataclass_field(default_factory=dict)
    tolerated_losses: list[ToleratedLoss] = dataclass_field(default_factory=list)


@dataclass(frozen=True)
class OkValue:
    """Successful branch result in a quorum parallel form."""

    value: Any
    branch_index: int


@dataclass(frozen=True)
class ErrValue:
    """Failed branch result in a quorum parallel form."""

    error: str
    error_type: str
    branch_index: int


@dataclass(frozen=True)
class QuorumResult:
    """Aggregate Try-typed result of a quorum-k parallel form.

    Each entry is an ``OkValue`` or ``ErrValue``.  Downstream code must
    use ``(oks ...)`` to project successes — direct field access is
    rejected at expansion time (check 6).
    """

    entries: tuple[Any, ...]
    quorum: int
    total: int


@dataclass(frozen=True)
class ToleratedLoss:
    """Record of a branch failure tolerated under quorum semantics."""

    path: str
    branch_index: int
    error: str
    error_type: str
    quorum: int
    total: int


@dataclass(frozen=True)
class ParkedValue:
    """Runtime marker for a subtree parked behind one or more open gates."""

    gates: tuple[OpenGateView, ...]
    halt_run: bool = False


@dataclass(frozen=True)
class WorkflowRuntimeResult:
    """Runtime result plus any open overseer gates materialized by live execution."""

    value: Any
    open_gates: tuple[OpenGateView, ...]
    tolerated_losses: tuple[ToleratedLoss, ...] = ()


def workflow_spec_to_program(
    workflow: WorkflowSpec,
    *,
    run_id: str,
    params: Mapping[str, Any] | None = None,
    issue: Issue | None = None,
    registry: ProfileRegistry | None = None,
    supervision: str = "autonomous",
    answered_gate_options: Mapping[str, str] | None = None,
    answered_retry_agent_counts: Mapping[str, int] | None = None,
    answered_gate_stakes: Mapping[str, Mapping[str, Any]] | None = None,
) -> Any:
    """Compile a ``WorkflowSpec`` into a production doeff Program."""

    workflow.expand()
    active_params: Mapping[str, Any] = params or {}
    active_registry: ProfileRegistry = registry or load_profile_registry_from_env()
    if supervision not in {"autonomous", "phase-checkpoints"}:
        raise ValueError(f"unsupported supervision policy: {supervision}")

    @do
    def program() -> Any:
        context = _RuntimeContext(
            workflow=workflow,
            run_id=run_id,
            params=active_params,
            registry=active_registry,
            issue=issue,
            supervision=supervision,
            answered_gate_options=answered_gate_options or {},
            answered_retry_agent_counts=answered_retry_agent_counts or {},
            answered_gate_stakes=answered_gate_stakes or {},
        )
        result: Any = None
        for index, form in enumerate(workflow.body):
            result = yield _execute_form(
                form,
                context,
                current_phase=None,
                path=str(index),
            )
            if isinstance(result, ParkedValue):
                # An open K5 gate is a decision boundary.  Do not evaluate
                # downstream phases against missing or parked dependencies;
                # resume with an answered gate if the overseer chooses proceed.
                break
        return WorkflowRuntimeResult(
            value=result,
            open_gates=tuple(context.open_gates.values()),
            tolerated_losses=tuple(context.tolerated_losses),
        )

    return program()


@do
def _execute_form(
    form: Any,
    context: _RuntimeContext,
    *,
    current_phase: str | None,
    path: str,
) -> Any:
    if isinstance(form, PhaseSpec):
        bindings_before_phase: dict[str, Any] = dict(context.bindings)
        result: Any = None
        for index, child_form in enumerate(form.body):
            child_path: str = _path_join(_path_join(path, form.name), str(index))
            result = yield _execute_form(
                child_form,
                context,
                current_phase=form.name,
                path=child_path,
            )
            if isinstance(result, ParkedValue):
                return result
        checkpoint = _checkpoint_gate_for_phase(
            context=context,
            phase=form,
            bindings_before_phase=bindings_before_phase,
        )
        if checkpoint is not None:
            return _park(context, checkpoint, halt_run=True)
        return result

    if isinstance(form, BindSpec):
        value: Any = yield _execute_expr(
            form.expr,
            context,
            current_phase=current_phase,
            path=path,
        )
        if isinstance(value, ParkedValue):
            _bind_parked_runtime_value(form.target, value, context)
        else:
            _bind_runtime_value(form.target, value, context)
        return value

    if isinstance(form, ArtifactSpec):
        return (yield _evaluate_value(form.value, context, path=_path_join(path, "artifact")))

    return (
        yield _execute_expr(
            form,
            context,
            current_phase=current_phase,
            path=path,
        )
    )


@do
def _execute_expr(  # noqa: PLR0911
    expr: Any,
    context: _RuntimeContext,
    *,
    current_phase: str | None,
    path: str,
) -> Any:
    if isinstance(expr, AgentSpec):
        return (
            yield _execute_agent(
                expr,
                context,
                current_phase=current_phase,
                path=path,
            )
        )
    if isinstance(expr, GateSpec):
        return (yield _execute_gate(expr, context, path=path))
    if isinstance(expr, MergeSpec):
        return (yield _execute_merge(expr, context, path=path))
    if isinstance(expr, TimeSpec):
        node_id: str = _node_id(context.workflow.name, path, "time")
        return (yield TimeCall(label=expr.label, run_id=context.run_id, node_id=node_id))
    if isinstance(expr, RandomSpec):
        node_id = _node_id(context.workflow.name, path, "random")
        random_spec: Any = yield _evaluate_value(
            expr.spec,
            context,
            path=_path_join(path, "spec"),
        )
        return (
            yield RandomCall(
                spec=random_spec,
                label=expr.label,
                run_id=context.run_id,
                node_id=node_id,
            )
        )
    if isinstance(expr, WorkspaceSpec):
        return (yield _materialize_workspace(expr, context, path=path))
    if isinstance(expr, ParallelSpec):
        return (
            yield _execute_parallel(
                expr.branches,
                context,
                current_phase=current_phase,
                path=path,
                fanout_label="parallel",
                quorum=expr.quorum,
            )
        )
    if isinstance(expr, ParallelForSpec):
        return (
            yield _execute_parallel(
                expr.branches,
                context,
                current_phase=current_phase,
                path=path,
                fanout_label="parallel-for",
            )
        )
    if isinstance(expr, LoopSpec):
        return (
            yield _execute_loop(
                expr,
                context,
                current_phase=current_phase,
                path=path,
            )
        )
    if isinstance(expr, (Ref, FieldRef, OksProjection, PromptExpr)):
        return (yield _evaluate_value(expr, context, path=path))
    raise TypeError(f"unsupported workflow runtime form: {type(expr).__name__}")


@do
def _execute_parallel(
    branches: Sequence[Any],
    context: _RuntimeContext,
    *,
    current_phase: str | None,
    path: str,
    fanout_label: str,
    quorum: int | None = None,
) -> Any:
    branch_count: int = len(branches)
    resolved_quorum: int = branch_count if quorum is None else quorum
    is_quorum_form: bool = resolved_quorum < branch_count

    tasks: list[Any] = []
    for branch_index, branch in enumerate(branches):
        branch_path: str = _path_join(path, f"{fanout_label}[{branch_index}]")
        branch_program: Any = _execute_expr(
            branch,
            context,
            current_phase=current_phase,
            path=branch_path,
        )
        if is_quorum_form:
            branch_program = _wrap_branch_for_quorum(branch_program, branch_index)
        task: Any = yield Spawn(branch_program)
        tasks.append(task)
    results = cast(tuple[Any, ...], (yield Gather(*tasks)))

    if not is_quorum_form:
        parked = _combine_parked_values(results)
        if parked is not None:
            return parked
        return results

    return _resolve_quorum(
        results, resolved_quorum, branch_count, path, context,
        current_phase=current_phase,
    )


@do
def _wrap_branch_for_quorum(branch_program: Any, branch_index: int) -> Any:
    """Execute a branch, catching failures as ``ErrValue`` for quorum aggregation.

    Branches that park behind an open gate are treated as failures (the
    branch could not produce a result).  The gate information is already
    recorded in ``context.open_gates`` by the time the ``ParkedValue``
    is returned, so it remains available for surface in the overseer view.
    """
    try:
        result: Any = yield branch_program
        if isinstance(result, ParkedValue):
            return ErrValue(
                error="branch parked behind open gate",
                error_type="ParkedValue",
                branch_index=branch_index,
            )
        return OkValue(value=result, branch_index=branch_index)
    except Exception as exc:
        return ErrValue(
            error=str(exc),
            error_type=type(exc).__name__,
            branch_index=branch_index,
        )


def _resolve_quorum(
    results: tuple[Any, ...],
    quorum: int,
    total: int,
    path: str,
    context: _RuntimeContext,
    *,
    current_phase: str | None = None,
) -> QuorumResult | ParkedValue:
    """Check quorum satisfaction and record tolerated losses.

    Parks behind a gate when fewer than *quorum* branches succeeded.
    If the gate was previously answered with ``proceed``, accepts partial
    results (records all failures as tolerated losses).
    """
    ok_count: int = sum(1 for r in results if isinstance(r, OkValue))
    err_count: int = sum(1 for r in results if isinstance(r, ErrValue))

    if ok_count < quorum:
        quorum_node_id: str = _node_id(context.workflow.name, path, "parallel")
        quorum_gate_id: str = f"{context.run_id}:{quorum_node_id}:quorum-not-met"
        if context.answered_gate_options.get(quorum_gate_id) == "proceed":
            _record_tolerated_losses(results, quorum, total, path, context)
            return QuorumResult(entries=tuple(results), quorum=quorum, total=total)
        return _park(
            context,
            _quorum_not_met_gate(
                context=context,
                path=path,
                quorum=quorum,
                total=total,
                succeeded=ok_count,
                failed=err_count,
                phase=current_phase,
            ),
        )

    _record_tolerated_losses(results, quorum, total, path, context)
    return QuorumResult(entries=tuple(results), quorum=quorum, total=total)


def _record_tolerated_losses(
    results: tuple[Any, ...],
    quorum: int,
    total: int,
    path: str,
    context: _RuntimeContext,
) -> None:
    """Record ErrValue entries as tolerated losses in the runtime context."""
    for entry in results:
        if isinstance(entry, ErrValue):
            context.tolerated_losses.append(
                ToleratedLoss(
                    path=path,
                    branch_index=entry.branch_index,
                    error=entry.error,
                    error_type=entry.error_type,
                    quorum=quorum,
                    total=total,
                )
            )


@do
def _execute_loop(
    spec: LoopSpec,
    context: _RuntimeContext,
    *,
    current_phase: str | None,
    path: str,
) -> Any:
    outer_bindings: dict[str, Any] = dict(context.bindings)
    last_value: Any = None
    for iteration_index in range(spec.max_iterations):
        body_bindings_before_iteration: set[str] = set(context.bindings)
        for form_index, form in enumerate(spec.body):
            # The iteration index is part of every body node's identity
            # (mirroring node_identity_fingerprint's loop_indices): agent
            # sessions are re-adopted by deterministic name, so a path
            # without the iteration made round 2 re-adopt round 1's DONE
            # session and return its stale result instead of doing new
            # work (observed live: a fix loop that never converged).
            form_path: str = _path_join(
                _path_join(path, f"loop[{iteration_index}]"), str(form_index)
            )
            last_value = yield _execute_form(
                form,
                context,
                current_phase=current_phase,
                path=form_path,
            )
            if isinstance(last_value, ParkedValue):
                _restore_loop_bindings(context, outer_bindings)
                return last_value
            predicate_result = cast(
                bool,
                (yield _evaluate_until(spec.until, context, path=_path_join(path, "until"))),
            )
            if predicate_result:
                _restore_loop_bindings(context, outer_bindings)
                return last_value

        local_names: set[str] = set(context.bindings) - body_bindings_before_iteration
        for local_name in local_names:
            context.bindings.pop(local_name, None)

        if iteration_index == spec.max_iterations - 1:
            break

    _restore_loop_bindings(context, outer_bindings)
    loop_node_id: str = _node_id(context.workflow.name, path, "loop")
    loop_gate_id: str = f"{context.run_id}:{loop_node_id}:loop-exhaustion"
    if context.answered_gate_options.get(loop_gate_id) == "proceed":
        return last_value
    return _park(
        context,
        _loop_exhaustion_gate(
            context=context,
            node_id=loop_node_id,
            predicate=spec.until,
            max_iterations=spec.max_iterations,
            phase=current_phase,
        ),
    )


@do
def _execute_agent(
    spec: AgentSpec,
    context: _RuntimeContext,
    *,
    current_phase: str | None,
    path: str,
) -> Any:
    effect = spec.to_effect(current_phase)
    node_id: str = _node_id(context.workflow.name, path, "agent")
    prompt_value = yield _evaluate_value(effect.prompt, context, path=_path_join(path, "prompt"))
    if isinstance(prompt_value, ParkedValue):
        return prompt_value
    prompt_text: str = _stringify_prompt_value(prompt_value)
    schema: dict[str, Any] = _schema_to_dict(effect.schema)
    validation_gate_id: str = _agent_result_validation_gate_id(context.run_id, node_id)
    retry_attempt: int = context.answered_retry_agent_counts.get(validation_gate_id, 0)
    prompt_context = ""
    if retry_attempt > 0:
        prompt_context = _agent_retry_prompt_context(
            attempt=retry_attempt,
            stakes=context.answered_gate_stakes.get(validation_gate_id, {}),
        )
    workspace: Workspace
    if effect.workspace is None:
        # The implicit per-agent workspace binds the same resume-stable
        # identity discipline as explicit workspace! nodes: derived from
        # (run_id, agent node identity), never random.
        workspace = cast(
            Workspace,
            (
                yield CreateWorkspace(
                    from_ref=_param_as_str(context.params.get("base_ref")),
                    issue=context.issue,
                    workspace_id=_workspace_identity(context.run_id, f"{node_id}/workspace"),
                )
            ),
        )
    else:
        workspace_value: Any = yield _evaluate_value(
            effect.workspace,
            context,
            path=_path_join(path, "workspace"),
        )
        if isinstance(workspace_value, ParkedValue):
            return workspace_value
        if not isinstance(workspace_value, Workspace):
            raise TypeError(f"agent workspace for {node_id} did not evaluate to Workspace")
        workspace = workspace_value

    profile_name, _source = resolve_agent_profile(
        effect,
        roles=context.workflow.roles,
        registry=context.registry,
    )
    profile: ProfileBinding = context.registry.resolve(profile_name)
    max_retries: int = _resolve_retry(effect.retry, effect.role, context.workflow.roles)
    task = AgentTask(
        run_id=context.run_id,
        node_id=node_id,
        attempt=retry_attempt,
        env=workspace,
        prompt=prompt_text,
        prompt_context=prompt_context,
        result_schema=schema,
        verification_class=effect.verification_class,
        agent_type=profile.adapter,
        name=effect.label,
        # ADR 0002: carry the phase for the observational progress producer
        # (monitor grouping). Not part of replay identity.
        phase=current_phase,
        profile=profile_name,
        model=profile.model,
        # Effort is an axis of the profile binding (L0 identity), never a
        # workflow/run parameter (ADR D7).
        effort=profile.effort,
        resolved_identity=ResolvedIdentity(
            adapter=profile.adapter,
            model=profile.model or "",
            identity=None,
            effort=profile.effort,
        ),
        max_retries=max_retries,
        # L-K4-3: the wall-clock deadline travels in the node spec; the
        # L2 loop observes it against its monotonic clock and the only
        # decision point is the K5 gate parked below.
        deadline_seconds=effect.deadline_seconds,
    )
    try:
        result = yield Agent(task)
    except AgentAttemptExhaustedError as error:
        return _park(
            context,
            _agent_result_validation_failed_gate(
                context=context,
                task=task,
                phase=current_phase,
                error=error,
            ),
        )
    except AgentDeadlineExceededError as error:
        return _park(
            context,
            _agent_deadline_exceeded_gate(
                context=context,
                task=task,
                phase=current_phase,
                error=error,
            ),
        )
    if workspace is not None:
        # D5 mechanized: a worker's output exists only once it is on the
        # workspace branch. Workers cannot be trusted to commit (prompt
        # promises are banned), so the runtime commits the workspace after
        # every successful agent node. Without this, merge! reconciles
        # branches identical to base and silently merges nothing (observed
        # live in the first end-to-end sample run).
        yield _commit_agent_workspace(workspace, node_id)
    return result


@do
def _commit_agent_workspace(workspace: Workspace, node_id: str) -> Any:
    """Commit a completed agent node's workspace changes, if any."""
    return (
        yield Commit(
            workspace=workspace,
            message=f"conductor: {node_id}",
            skip_if_clean=True,
        )
    )


@do
def _execute_gate(spec: GateSpec, context: _RuntimeContext, *, path: str) -> Any:
    workspace: Workspace | None = None
    if spec.workspace is not None:
        workspace_value: Any = yield _evaluate_value(
            spec.workspace,
            context,
            path=_path_join(path, "workspace"),
        )
        if isinstance(workspace_value, ParkedValue):
            return workspace_value
        if not isinstance(workspace_value, Workspace):
            raise TypeError("gate workspace did not evaluate to Workspace")
        workspace = workspace_value
    return cast(ExecResult, (yield Exec(cmd=spec.cmd, workspace=workspace, timeout=spec.timeout)))


@do
def _execute_merge(spec: MergeSpec, context: _RuntimeContext, *, path: str) -> Any:
    node_id: str = _node_id(context.workflow.name, path, "merge")
    workspaces: list[Workspace] = []
    for workspace_index, workspace_expr in enumerate(spec.workspaces):
        workspace_value: Any = yield _evaluate_value(
            workspace_expr,
            context,
            path=_path_join(path, f"workspaces[{workspace_index}]"),
        )
        if isinstance(workspace_value, ParkedValue):
            return workspace_value
        if not isinstance(workspace_value, Workspace):
            raise TypeError("merge! workspaces must evaluate to Workspace values")
        workspaces.append(workspace_value)
    merge_result = cast(
        MergeWorkspacesResult,
        (
            yield MergeWorkspaces(
                workspace_id=_workspace_identity(context.run_id, node_id),
                workspaces=tuple(workspaces),
                strategy=MergeStrategy(spec.strategy),
            )
        ),
    )
    if not merge_result.merged or merge_result.workspace is None:
        return _park(
            context,
            _merge_conflict_gate(
                context=context,
                node_id=node_id,
                merge_result=merge_result,
                workspaces=tuple(workspaces),
            ),
        )
    return merge_result.workspace


@do
def _materialize_workspace(
    spec: WorkspaceSpec,
    context: _RuntimeContext,
    *,
    path: str,
) -> Any:
    # Identity belongs to the workspace EXPRESSION (its source-order
    # occurrence), never to the evaluation site: a module-level
    # `(setv ws (workspace! ...))` shared by several nodes is ONE
    # workspace.  Keying by evaluation path gave every consumer its own
    # fresh worktree — a gate then tested a different tree than the
    # implementer wrote to, the exact false-positive class this module's
    # resume-stability discipline exists to kill.  Occurrence numbers are
    # loader-reset per module exec, so identical source yields identical
    # identities across processes.
    node_id: str = _node_id(context.workflow.name, f"workspace!{spec.occurrence}", "workspace")
    cached_workspace: Workspace | None = context.workspace_cache.get(node_id)
    if cached_workspace is not None:
        return cached_workspace

    from_ref_value: Any = None
    if spec.from_ref is not None:
        from_ref_value = yield _evaluate_value(
            spec.from_ref,
            context,
            path=_path_join(path, "from"),
        )
    workspace = cast(
        Workspace,
        (
            yield CreateWorkspace(
                workspace_id=_workspace_identity(context.run_id, node_id),
                repo=spec.repo or "default",
                from_ref=_param_as_str(from_ref_value),
                issue=context.issue,
            )
        ),
    )
    context.workspace_cache[node_id] = workspace
    return workspace


@do
def _evaluate_value(  # noqa: PLR0911, PLR0912, PLR0915
    value: Any,
    context: _RuntimeContext,
    *,
    path: str,
) -> Any:
    if isinstance(value, Ref):
        if value.name in context.bindings:
            return context.bindings[value.name]
        if value.name in context.params:
            return context.params[value.name]
        raise KeyError(f"undefined runtime reference: {value.name}")
    if isinstance(value, FieldRef):
        source_value: Any = yield _evaluate_value(
            value.source,
            context,
            path=_path_join(path, "source"),
        )
        if isinstance(source_value, ParkedValue):
            return source_value
        return _read_field(source_value, value.field_name)
    if isinstance(value, OksProjection):
        oks_source: Any = yield _evaluate_value(
            value.source, context, path=_path_join(path, "source"),
        )
        if isinstance(oks_source, ParkedValue):
            return oks_source
        if isinstance(oks_source, QuorumResult):
            return tuple(
                entry.value for entry in oks_source.entries if isinstance(entry, OkValue)
            )
        return oks_source
    if isinstance(value, PromptExpr):
        parts: list[str] = []
        parked_values: list[ParkedValue] = []
        for part_index, part in enumerate(value.parts):
            part_value: Any = yield _evaluate_value(
                part,
                context,
                path=_path_join(path, f"[{part_index}]"),
            )
            if isinstance(part_value, ParkedValue):
                parked_values.append(part_value)
                continue
            parts.append(_stringify_prompt_value(part_value))
        parked = _combine_parked_values(parked_values)
        if parked is not None:
            return parked
        return "".join(parts)
    if isinstance(value, WorkspaceSpec):
        return (yield _materialize_workspace(value, context, path=path))
    if isinstance(value, tuple):
        items: list[Any] = []
        for item_index, item in enumerate(value):
            items.append(
                (yield _evaluate_value(item, context, path=_path_join(path, f"[{item_index}]")))
            )
        parked = _combine_parked_values(items)
        if parked is not None:
            return parked
        return tuple(items)
    if isinstance(value, list):
        items = []
        for item_index, item in enumerate(value):
            items.append(
                (yield _evaluate_value(item, context, path=_path_join(path, f"[{item_index}]")))
            )
        parked = _combine_parked_values(items)
        if parked is not None:
            return parked
        return items
    if isinstance(value, dict):
        evaluated: dict[Any, Any] = {}
        parked_values: list[ParkedValue] = []
        for entry_index, (key, item) in enumerate(value.items()):
            evaluated_key: Any = yield _evaluate_value(
                key,
                context,
                path=_path_join(path, f"key[{entry_index}]"),
            )
            evaluated_value: Any = yield _evaluate_value(
                item,
                context,
                path=_path_join(path, f"value[{entry_index}]"),
            )
            if isinstance(evaluated_key, ParkedValue):
                parked_values.append(evaluated_key)
                continue
            if isinstance(evaluated_value, ParkedValue):
                parked_values.append(evaluated_value)
                continue
            evaluated[evaluated_key] = evaluated_value
        parked = _combine_parked_values(parked_values)
        if parked is not None:
            return parked
        return evaluated
    if isinstance(value, set):
        items = []
        for item in value:
            items.append((yield _evaluate_value(item, context, path=path)))
        parked = _combine_parked_values(items)
        if parked is not None:
            return parked
        return set(items)
    if isinstance(value, frozenset):
        items = []
        for item in value:
            items.append((yield _evaluate_value(item, context, path=path)))
        parked = _combine_parked_values(items)
        if parked is not None:
            return parked
        return frozenset(items)
    return value


@do
def _evaluate_until(predicate: Any, context: _RuntimeContext, *, path: str) -> Any:
    if callable(predicate):
        result: object = predicate(dict(context.bindings))
        return bool(result)
    if isinstance(predicate, Ref):
        return bool((yield _evaluate_value(predicate, context, path=path)))
    if isinstance(predicate, str):
        if predicate.endswith("_passed"):
            binding_name: str = predicate.removesuffix("_passed")
            binding_value: Any = context.bindings.get(binding_name)
            return _value_passed(binding_value)
        return bool(context.bindings.get(predicate))
    return bool(predicate)


def _bind_runtime_value(target: str | Sequence[str], value: Any, context: _RuntimeContext) -> None:
    if isinstance(target, str):
        context.bindings[target] = value
        return
    if not isinstance(value, Sequence):
        raise TypeError(f"cannot destructure non-sequence value into {target!r}")
    if len(value) != len(target):
        raise ValueError(f"cannot destructure {len(value)} values into {len(target)} names")
    for index, name in enumerate(target):
        context.bindings[str(name)] = value[index]


def _bind_parked_runtime_value(
    target: str | Sequence[str],
    value: ParkedValue,
    context: _RuntimeContext,
) -> None:
    if isinstance(target, str):
        context.bindings[target] = value
        return
    for name in target:
        context.bindings[str(name)] = value


def _restore_loop_bindings(
    context: _RuntimeContext,
    outer_bindings: Mapping[str, Any],
) -> None:
    context.bindings.clear()
    context.bindings.update(outer_bindings)


def _park(
    context: _RuntimeContext,
    gate: OpenGateView,
    *,
    halt_run: bool = False,
) -> ParkedValue:
    context.open_gates[gate.gate_id] = gate
    return ParkedValue(gates=(gate,), halt_run=halt_run)


def _combine_parked_values(values: Iterable[Any]) -> ParkedValue | None:
    gates_by_id: dict[str, OpenGateView] = {}
    halt_run = False
    for value in values:
        if not isinstance(value, ParkedValue):
            continue
        halt_run = halt_run or value.halt_run
        for gate in value.gates:
            gates_by_id[gate.gate_id] = gate
    if not gates_by_id:
        return None
    return ParkedValue(gates=tuple(gates_by_id.values()), halt_run=halt_run)


def _agent_result_validation_failed_gate(
    *,
    context: _RuntimeContext,
    task: AgentTask,
    phase: str | None,
    error: AgentAttemptExhaustedError,
) -> OpenGateView:
    return OpenGateView(
        gate_id=_agent_result_validation_gate_id(context.run_id, task.node_id),
        workflow_id=context.run_id,
        node_id=task.node_id,
        phase=phase,
        reason="agent result validation failed",
        stakes={
            "verification_class": task.verification_class,
            "blast_radius": "dependent-subtree",
            "reversibility": "retryable",
            "profile": task.profile,
            "attempts": error.attempts,
            "session_id": task.session_id,
            "last_error_kind": error.last_error.kind.value,
            "last_error_message": error.last_error.message,
        },
        options=_agent_result_validation_gate_options(),
    )


def _agent_result_validation_gate_id(run_id: str, node_id: str) -> str:
    return f"{run_id}:{node_id}:agent-result-validation-failed"


def _agent_retry_prompt_context(
    *,
    attempt: int,
    stakes: Mapping[str, Any],
) -> str:
    last_error_kind = stakes.get("last_error_kind", "unknown")
    last_error_message = stakes.get("last_error_message", "no prior error detail recorded")
    previous_session_id = stakes.get("session_id", "unknown")
    return (
        "\n\n"
        "## Previous structured result failure (managed by doeff-conductor)\n"
        f"This is retry attempt {attempt} for the same agent node. "
        "The previous worker attempt finished without a schema-valid structured result.\n"
        f"- previous_session_id: {previous_session_id}\n"
        f"- last_error_kind: {last_error_kind}\n"
        f"- last_error_message: {last_error_message}\n\n"
        "Do not repeat the same result-shape mistake. Complete the domain task, then return "
        "one structured result that satisfies the schema shown in the managed result contract."
    )


def _agent_result_validation_gate_options() -> tuple[GateOption, ...]:
    return (
        GateOption(
            name="retry-agent",
            outcome="resume",
            description=(
                "Launch a fresh worker attempt for this agent node with the last "
                "structured-result validation error included in the prompt."
            ),
        ),
        GateOption(
            name="redirect",
            outcome="resume",
            description="Edit workflow state, result artifacts, or inputs, then resume.",
        ),
        GateOption(
            name="abort",
            outcome="abort",
            description="Abort the dependent subtree and preserve the gate outcome.",
        ),
    )


def _agent_deadline_exceeded_gate(
    *,
    context: _RuntimeContext,
    task: AgentTask,
    phase: str | None,
    error: AgentDeadlineExceededError,
) -> OpenGateView:
    """K5 gate for wall-clock deadline exhaustion (L-K4-3).

    Named sibling of ``budget-exhausted``: same gate family (closure
    park + journaled answer), distinct reason and options because the
    session may still be alive and the only forward path is RENEWING
    the deadline window — never accepting a partial result.
    """
    return OpenGateView(
        gate_id=f"{context.run_id}:{task.node_id}:deadline-exceeded",
        workflow_id=context.run_id,
        node_id=task.node_id,
        phase=phase,
        reason="wall-clock deadline exceeded",
        stakes={
            "verification_class": task.verification_class,
            "blast_radius": "dependent-subtree",
            "reversibility": "retryable",
            "profile": task.profile,
            "deadline_seconds": error.deadline_seconds,
            "elapsed_seconds": error.elapsed_seconds,
            "session_id": task.session_id,
        },
        options=(
            GateOption(
                name="extend",
                outcome="resume",
                description=(
                    "Grant one more deadline window and re-await the node "
                    "(renewal = this journaled answer; no automatic extension exists)."
                ),
            ),
            GateOption(
                name="redirect",
                outcome="resume",
                description="Edit workflow state or inputs, then resume from the journal prefix.",
            ),
            GateOption(
                name="abort",
                outcome="abort",
                description="Abort the dependent subtree and preserve the gate outcome.",
            ),
        ),
    )


def _merge_conflict_gate(
    *,
    context: _RuntimeContext,
    node_id: str,
    merge_result: MergeWorkspacesResult,
    workspaces: tuple[Workspace, ...],
) -> OpenGateView:
    """D5 closure gate: merge conflict parks the run with conflict details."""
    conflicted_files: list[str] = []
    source_workspace_ids: list[str] = []
    conflict: MergeConflict
    for conflict in merge_result.conflicts:
        conflicted_files.extend(conflict.files)
        source_workspace_ids.append(conflict.workspace.id)
    return OpenGateView(
        gate_id=f"{context.run_id}:{node_id}:merge-conflict",
        workflow_id=context.run_id,
        node_id=node_id,
        phase=None,
        reason="merge conflict",
        stakes={
            "conflicted_files": conflicted_files,
            "source_workspaces": source_workspace_ids,
            "merge_message": merge_result.message or "",
            "verification_class": "merge",
            "blast_radius": "dependent-subtree",
            "reversibility": "retryable",
        },
        options=(
            GateOption(
                name="retry-merge",
                outcome="resume",
                description="Retry the merge after resolving conflicts in the source workspace(s).",
            ),
            GateOption(
                name="abort",
                outcome="abort",
                description="Abort the dependent subtree and preserve the gate outcome.",
            ),
        ),
    )


def _loop_exhaustion_gate(
    *,
    context: _RuntimeContext,
    node_id: str,
    predicate: Any,
    max_iterations: int,
    phase: str | None,
) -> OpenGateView:
    """Closure gate: loop predicate exhaustion parks with last-state option."""
    return OpenGateView(
        gate_id=f"{context.run_id}:{node_id}:loop-exhaustion",
        workflow_id=context.run_id,
        node_id=node_id,
        phase=phase,
        reason="loop predicate exhaustion",
        stakes={
            "predicate": str(predicate),
            "max_iterations": max_iterations,
            "verification_class": "loop",
            "blast_radius": "dependent-subtree",
            "reversibility": "retryable",
        },
        options=(
            GateOption(
                name="proceed",
                outcome="resume",
                description="Accept the last loop iteration state and continue.",
            ),
            GateOption(
                name="abort",
                outcome="abort",
                description="Abort the dependent subtree and preserve the gate outcome.",
            ),
        ),
    )


def _quorum_not_met_gate(
    *,
    context: _RuntimeContext,
    path: str,
    quorum: int,
    total: int,
    succeeded: int,
    failed: int,
    phase: str | None,
) -> OpenGateView:
    """Closure gate: quorum shortfall parks with partial-accept option."""
    quorum_node_id: str = _node_id(context.workflow.name, path, "parallel")
    return OpenGateView(
        gate_id=f"{context.run_id}:{quorum_node_id}:quorum-not-met",
        workflow_id=context.run_id,
        node_id=quorum_node_id,
        phase=phase,
        reason="quorum not met",
        stakes={
            "quorum": quorum,
            "total": total,
            "succeeded": succeeded,
            "failed": failed,
            "verification_class": "quorum",
            "blast_radius": "dependent-subtree",
            "reversibility": "non-retryable",
        },
        options=(
            GateOption(
                name="proceed",
                outcome="resume",
                description="Accept partial results and continue with available successes.",
            ),
            GateOption(
                name="abort",
                outcome="abort",
                description="Abort the dependent subtree due to insufficient quorum.",
            ),
        ),
    )


def _checkpoint_gate_for_phase(
    *,
    context: _RuntimeContext,
    phase: PhaseSpec,
    bindings_before_phase: Mapping[str, Any],
) -> OpenGateView | None:
    if context.supervision != "phase-checkpoints":
        return None
    gate_id = f"{context.run_id}:checkpoint:{phase.name}"
    if context.answered_gate_options.get(gate_id) in {"proceed", "redirect"}:
        return None

    binding_deltas = _binding_deltas(bindings_before_phase, context.bindings)
    artifact_summaries = {
        name: _summarize_artifact(context.bindings[name])
        for name in binding_deltas
        if name in context.bindings
    }
    return OpenGateView(
        gate_id=gate_id,
        workflow_id=context.run_id,
        node_id=f"checkpoint:{phase.name}",
        phase=phase.name,
        reason="phase checkpoint",
        stakes={
            "phase": phase.name,
            "level": phase.stakes,
            "verification_class": "checkpoint",
            "blast_radius": "dependent-subtree",
            "reversibility": "abortable",
            "binding_deltas": binding_deltas,
            "artifact_summaries": artifact_summaries,
        },
        options=_gate_options(),
    )


def _binding_deltas(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> list[str]:
    changed: list[str] = []
    for name in sorted(after):
        if name not in before:
            changed.append(name)
            continue
        if _jsonable(before[name]) != _jsonable(after[name]):
            changed.append(name)
    return changed


def _summarize_artifact(value: Any) -> str:
    if isinstance(value, ParkedValue):
        return "parked behind open gate"
    encoded = json.dumps(_jsonable(value), sort_keys=True, ensure_ascii=True)
    if len(encoded) <= 160:
        return encoded
    return f"{encoded[:157]}..."


def _gate_options() -> tuple[GateOption, ...]:
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
            description="Abort the dependent subtree and preserve the gate outcome.",
        ),
    )


def _resolve_retry(
    explicit_retry: int | None,
    role_name: str,
    roles: Mapping[str, Mapping[str, Any]],
) -> int:
    if explicit_retry is not None:
        return explicit_retry
    role_spec: Mapping[str, Any] = roles[role_name]
    role_retry: object | None = role_spec.get("retry")
    if role_retry is None:
        return 2
    if not isinstance(role_retry, int) or isinstance(role_retry, bool):
        raise TypeError(f"role {role_name!r} retry must be an integer")
    return role_retry


def _schema_to_dict(schema: Any) -> dict[str, Any]:
    if isinstance(schema, dict):
        return schema
    if hasattr(schema, "model_json_schema"):
        model_json_schema = schema.model_json_schema
        if not callable(model_json_schema):
            raise TypeError("schema model_json_schema attribute must be callable")
        generated_schema: object = model_json_schema()
        if isinstance(generated_schema, dict):
            return generated_schema
    raise TypeError("agent! schema must be a JSON-schema dictionary or pydantic model")


def _read_field(source_value: Any, field_name: str) -> Any:
    if isinstance(source_value, Mapping):
        if field_name not in source_value:
            raise KeyError(f"mapping result has no field {field_name!r}")
        return source_value[field_name]
    source_vars: dict[str, Any] = vars(source_value)
    if field_name not in source_vars:
        raise AttributeError(f"{type(source_value).__name__} has no field {field_name!r}")
    return source_vars[field_name]


def _value_passed(value: Any) -> bool:
    if isinstance(value, ParkedValue):
        return False
    if isinstance(value, ExecResult):
        return value.passed
    if isinstance(value, Mapping):
        passed_value: object | None = value.get("passed")
        if isinstance(passed_value, bool):
            return passed_value
    return bool(value)


def _stringify_prompt_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return str(value)
    return json.dumps(_jsonable(value), sort_keys=True, ensure_ascii=True)


def _jsonable(value: Any) -> Any:  # noqa: PLR0911
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, type):
        return value.__name__
    if hasattr(value, "to_dict"):
        to_dict = value.to_dict
        if callable(to_dict):
            return _jsonable(to_dict())
    return str(value)


def _param_as_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return str(value)
    return value


def _workspace_identity(run_id: str, node_key: str) -> str:
    """Derive the resume-stable workspace identity for one workspace node.

    Deterministic in ``(run_id, node_key)`` so re-running the same run id
    re-binds the same branch and worktree. The digest must cover BOTH
    inputs: slugs are lossy (every punctuation character collapses to
    ``-``), so a node-key-only digest let run ids differing only in
    punctuation collide into one identity — the second run then silently
    re-adopted the first run's branch including its commits (review
    finding F1, reproduced live).
    """
    digest: str = hashlib.sha256(f"{run_id}\n{node_key}".encode()).hexdigest()[:8]
    slug: str = _slugify(node_key)[-48:].strip("-")
    return f"{_slugify(run_id)}-{slug}-{digest}"


def _slugify(text: str) -> str:
    return "".join(character if character.isalnum() else "-" for character in text)


def _node_id(workflow_name: str, path: str, kind: str) -> str:
    if path:
        return f"{workflow_name}/{path}/{kind}"
    return f"{workflow_name}/{kind}"


def _path_join(prefix: str, child: str) -> str:
    if not prefix:
        return child
    return f"{prefix}/{child}"
