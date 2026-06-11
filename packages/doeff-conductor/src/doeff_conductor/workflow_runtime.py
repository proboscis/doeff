"""Production interpreter for ``WorkflowSpec`` request artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from dataclasses import field as dataclass_field
from datetime import datetime, timezone
from random import Random
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
from doeff_conductor.effects import Agent, AgentTask, Commit, CreateWorkspace, Exec, MergeWorkspaces
from doeff_conductor.environment import (
    ProfileBinding,
    ProfileRegistry,
    load_profile_registry_from_env,
)
from doeff_conductor.replay_keying import ResolvedIdentity
from doeff_conductor.types import ExecResult, Issue, MergeStrategy, MergeWorkspacesResult, Workspace
from doeff_conductor.verbs import resolve_agent_profile


@dataclass
class _RuntimeContext:
    workflow: WorkflowSpec
    run_id: str
    params: Mapping[str, Any]
    registry: ProfileRegistry
    issue: Issue | None
    bindings: dict[str, Any] = dataclass_field(default_factory=dict)
    # Keyed by the workspace EXPRESSION's source-order occurrence, NOT by
    # Python object id and NOT by evaluation site — one workspace! value
    # shared by several nodes must bind one workspace, identically across
    # process restarts (resume stability).
    workspace_cache: dict[str, Workspace] = dataclass_field(default_factory=dict)


def workflow_spec_to_program(
    workflow: WorkflowSpec,
    *,
    run_id: str,
    params: Mapping[str, Any] | None = None,
    issue: Issue | None = None,
    registry: ProfileRegistry | None = None,
) -> Any:
    """Compile a ``WorkflowSpec`` into a production doeff Program."""

    workflow.expand()
    active_params: Mapping[str, Any] = params or {}
    active_registry: ProfileRegistry = registry or load_profile_registry_from_env()

    @do
    def program() -> Any:
        context = _RuntimeContext(
            workflow=workflow,
            run_id=run_id,
            params=active_params,
            registry=active_registry,
            issue=issue,
        )
        result: Any = None
        for index, form in enumerate(workflow.body):
            result = yield _execute_form(
                form,
                context,
                current_phase=None,
                path=str(index),
            )
        return result

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
        result: Any = None
        for index, child_form in enumerate(form.body):
            child_path: str = _path_join(_path_join(path, form.name), str(index))
            result = yield _execute_form(
                child_form,
                context,
                current_phase=form.name,
                path=child_path,
            )
        return result

    if isinstance(form, BindSpec):
        value: Any = yield _execute_expr(
            form.expr,
            context,
            current_phase=current_phase,
            path=path,
        )
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
        return datetime.now(timezone.utc).isoformat()
    if isinstance(expr, RandomSpec):
        return _evaluate_random(expr)
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
) -> Any:
    tasks: list[Any] = []
    for branch_index, branch in enumerate(branches):
        branch_path: str = _path_join(path, f"{fanout_label}[{branch_index}]")
        task: Any = yield Spawn(
            _execute_expr(
                branch,
                context,
                current_phase=current_phase,
                path=branch_path,
            )
        )
        tasks.append(task)
    results = cast(tuple[Any, ...], (yield Gather(*tasks)))
    return results


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
    raise RuntimeError(f"loop predicate {spec.until!r} did not pass within {spec.max_iterations}")


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
    prompt_text: str = _stringify_prompt_value(
        (yield _evaluate_value(effect.prompt, context, path=_path_join(path, "prompt")))
    )
    schema: dict[str, Any] = _schema_to_dict(effect.schema)
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
    result = yield Agent(
        AgentTask(
            run_id=context.run_id,
            node_id=node_id,
            attempt=0,
            env=workspace,
            prompt=prompt_text,
            result_schema=schema,
            verification_class=effect.verification_class,
            agent_type=profile.adapter,
            name=effect.label,
            profile=profile_name,
            model=profile.model,
            # Effort is an axis of the profile binding (L0 identity), never a
            # workflow/run parameter (ADR D7).
            effort=profile.effort,
            resolved_identity=ResolvedIdentity(
                adapter=profile.adapter,
                model=profile.model,
                identity=None,
                effort=profile.effort,
            ),
            max_retries=max_retries,
        )
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
        raise RuntimeError(f"workspace merge failed: {merge_result.message}")
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
def _evaluate_value(  # noqa: PLR0911, PLR0912
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
        return _read_field(source_value, value.field_name)
    if isinstance(value, OksProjection):
        return (yield _evaluate_value(value.source, context, path=_path_join(path, "source")))
    if isinstance(value, PromptExpr):
        parts: list[str] = []
        for part_index, part in enumerate(value.parts):
            part_value: Any = yield _evaluate_value(
                part,
                context,
                path=_path_join(path, f"[{part_index}]"),
            )
            parts.append(_stringify_prompt_value(part_value))
        return "".join(parts)
    if isinstance(value, WorkspaceSpec):
        return (yield _materialize_workspace(value, context, path=path))
    if isinstance(value, tuple):
        items: list[Any] = []
        for item_index, item in enumerate(value):
            items.append(
                (yield _evaluate_value(item, context, path=_path_join(path, f"[{item_index}]")))
            )
        return tuple(items)
    if isinstance(value, list):
        items = []
        for item_index, item in enumerate(value):
            items.append(
                (yield _evaluate_value(item, context, path=_path_join(path, f"[{item_index}]")))
            )
        return items
    if isinstance(value, dict):
        evaluated: dict[Any, Any] = {}
        for entry_index, (key, item) in enumerate(value.items()):
            evaluated_key: Any = yield _evaluate_value(
                key,
                context,
                path=_path_join(path, f"key[{entry_index}]"),
            )
            evaluated[evaluated_key] = yield _evaluate_value(
                item,
                context,
                path=_path_join(path, f"value[{entry_index}]"),
            )
        return evaluated
    if isinstance(value, set):
        items = []
        for item in value:
            items.append((yield _evaluate_value(item, context, path=path)))
        return set(items)
    if isinstance(value, frozenset):
        items = []
        for item in value:
            items.append((yield _evaluate_value(item, context, path=path)))
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


def _evaluate_random(spec: RandomSpec) -> Any:
    random_source = Random(0)
    if isinstance(spec.spec, Mapping):
        kind: object | None = spec.spec.get("kind")
        if kind == "choice":
            values: object | None = spec.spec.get("values")
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                raise TypeError("random! choice requires a non-string sequence of values")
            return random_source.choice(list(values))
    return random_source.random()


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


def _restore_loop_bindings(
    context: _RuntimeContext,
    outer_bindings: Mapping[str, Any],
) -> None:
    context.bindings.clear()
    context.bindings.update(outer_bindings)


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
