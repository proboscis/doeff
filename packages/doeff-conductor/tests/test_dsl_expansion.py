from __future__ import annotations

import pytest
from doeff_conductor.dsl import (
    WorkflowExpansionError,
    agent_bang,
    artifact,
    ask,
    bind,
    defphase,
    defworkflow,
    field,
    gate_bang,
    local,
    loop,
    merge_bang,
    oks,
    parallel,
    parallel_for,
    pipeline,
    prompt,
    raw_node,
    ref,
    workspace_bang,
)

RESULT_SCHEMA = {"type": "object", "required": ["status"], "properties": {"status": {"type": "string"}}}
VERDICT_SCHEMA = {
    "type": "object",
    "required": ["verdict", "findings"],
    "properties": {"verdict": {"enum": ["PASS", "CHANGES_REQUESTED"]}, "findings": {"type": "array"}},
}


def valid_agent(label: str = "impl", **overrides: object) -> object:
    fields = {
        "role": "implementer",
        "verification_class": "test-verifiable",
        "prompt": f"implement {label}",
        "schema": RESULT_SCHEMA,
        "workspace": workspace_bang(from_="main"),
        "files": {f"{label}.py"},
        "label": label,
    }
    fields.update(overrides)
    return agent_bang(**fields)


def valid_workflow(*body: object) -> object:
    return defworkflow(
        "valid",
        params={"base_ref": str},
        roles={"implementer": {"profile": "cheap-coder", "retry": 3}},
        body=list(body),
    )


def test_check_1_rejects_unknown_phase_use() -> None:
    workflow = valid_workflow(valid_agent(phase="Missing"))

    with pytest.raises(WorkflowExpansionError, match="phase"):
        workflow.expand()


def test_check_1_rejects_malformed_parallel_join() -> None:
    workflow = valid_workflow(bind(["first", "second"], parallel(valid_agent("one"))))

    with pytest.raises(WorkflowExpansionError, match="join"):
        workflow.expand()


def test_check_2_rejects_agent_without_schema_or_class() -> None:
    workflow = valid_workflow(agent_bang(role="implementer", prompt="missing", schema=None))

    with pytest.raises(WorkflowExpansionError, match="schema"):
        workflow.expand()


def test_check_3_rejects_unknown_role() -> None:
    workflow = valid_workflow(valid_agent(role="missing-role"))

    with pytest.raises(WorkflowExpansionError, match="role"):
        workflow.expand()


def test_check_4_rejects_shared_workspace_parallel_file_overlap() -> None:
    shared_workspace = workspace_bang(from_="main")
    workflow = valid_workflow(
        bind(
            ["a", "b"],
            parallel(
                valid_agent("a", workspace=shared_workspace, files={"shared.py"}),
                valid_agent("b", workspace=shared_workspace, files={"shared.py"}),
            ),
        )
    )

    with pytest.raises(WorkflowExpansionError, match="files"):
        workflow.expand()


def test_check_5_rejects_non_closing_raw_node() -> None:
    workflow = valid_workflow(raw_node("manual side effect"))

    with pytest.raises(WorkflowExpansionError, match="closure"):
        workflow.expand()


def test_check_6_rejects_direct_deref_of_quorum_try_binding() -> None:
    workflow = valid_workflow(
        bind(
            "impls",
            parallel(
                valid_agent("a", workspace=workspace_bang(from_="main-a")),
                valid_agent("b", workspace=workspace_bang(from_="main-b")),
                quorum=1,
            ),
        ),
        artifact(field(ref("impls"), "status")),
    )

    with pytest.raises(WorkflowExpansionError, match="Try"):
        workflow.expand()


def test_check_6_allows_explicit_ok_projection_of_quorum_binding() -> None:
    shared_workspace = workspace_bang(from_="main")
    workflow = valid_workflow(
        bind(
            "impls",
            parallel(
                valid_agent("a", workspace=shared_workspace, files={"a.py"}),
                valid_agent("b", workspace=shared_workspace, files={"b.py"}),
                quorum=1,
            ),
        ),
        artifact(oks(ref("impls"))),
    )

    expanded = workflow.expand()

    assert expanded.bindings["impls"].is_try


def test_check_7_rejects_invalid_budget_annotation() -> None:
    workflow = valid_workflow(valid_agent(budget="ten tokens"))

    with pytest.raises(WorkflowExpansionError, match="budget"):
        workflow.expand()


def test_check_7_rejects_budget_sum_above_workflow_limit() -> None:
    workflow = defworkflow(
        "budgeted",
        params={},
        roles={"implementer": {"profile": "cheap-coder"}},
        budget="100",
        body=[valid_agent("a", budget="60"), valid_agent("b", budget="60")],
    )

    with pytest.raises(WorkflowExpansionError, match="budget"):
        workflow.expand()


def test_check_8_rejects_dynamic_scoping_constructs() -> None:
    workflow = valid_workflow(bind("x", ask("profile")))

    with pytest.raises(WorkflowExpansionError, match="binding locality"):
        workflow.expand()


def test_check_8_rejects_pipeline_as_open_v1_surface() -> None:
    workflow = valid_workflow(pipeline(valid_agent()))

    with pytest.raises(WorkflowExpansionError, match="pipeline"):
        workflow.expand()


def test_check_8_rejects_local_dynamic_scope() -> None:
    workflow = valid_workflow(local({"profile": "cheap-coder"}, valid_agent()))

    with pytest.raises(WorkflowExpansionError, match="binding locality"):
        workflow.expand()


def test_check_9_rejects_undefined_reference() -> None:
    workflow = valid_workflow(artifact(ref("missing")))

    with pytest.raises(WorkflowExpansionError, match="undefined"):
        workflow.expand()


def test_check_9_rejects_unconsumed_binding() -> None:
    workflow = valid_workflow(bind("unused", valid_agent()))

    with pytest.raises(WorkflowExpansionError, match="unconsumed"):
        workflow.expand()


def test_check_9_allows_reference_to_declared_param() -> None:
    workflow = valid_workflow(artifact(ref("base_ref")))

    expanded = workflow.expand()

    assert expanded.bindings["base_ref"].source_kind == "param"


def test_check_9_rejects_binding_that_shadows_param() -> None:
    workflow = valid_workflow(bind("base_ref", valid_agent()))

    with pytest.raises(WorkflowExpansionError, match="duplicate binding"):
        workflow.expand()


def test_parallel_for_expands_static_fanout() -> None:
    workflow = valid_workflow(
        bind(
            "impls",
            parallel_for(
                "task",
                ["auth", "search", "export"],
                lambda task: valid_agent(task, prompt=prompt("implement ", task)),
            ),
        ),
        artifact(ref("impls")),
    )

    expanded = workflow.expand()

    assert [node.node_id for node in expanded.nodes if node.kind == "agent"] == [
        "valid/0/parallel-for[0]/agent",
        "valid/0/parallel-for[1]/agent",
        "valid/0/parallel-for[2]/agent",
    ]


def test_k2_k3_shaped_fixture_expands_cleanly() -> None:
    base = workspace_bang(from_="main")
    impl_workspaces = [
        workspace_bang(from_=f"main-impl-{index}")
        for index in range(5)
    ]
    test_workspace = workspace_bang(from_="main-tests")
    gate_workspace = workspace_bang(from_="main-gate")

    workflow = defworkflow(
        "k2_k3_reference_shape",
        params={"issue": str, "base_ref": str},
        roles={
            "implementer": {"profile": "cheap-coder", "retry": 3},
            "fixer": {"profile": "cheap-coder", "retry": 3},
            "test_writer": {"profile": "cheap-coder", "retry": 2},
            "reviewer": {"profile": "cheap-reviewer", "retry": 1},
        },
        body=[
            defphase(
                "Implement",
                stakes="normal",
                body=[
                    bind(
                        "impls",
                        parallel(
                            *[
                                valid_agent(
                                    f"impl-{index}",
                                    role="implementer",
                                    workspace=impl_workspaces[index],
                                    prompt=prompt("implement variant ", index),
                                )
                                for index in range(5)
                            ]
                        ),
                    )
                ],
            ),
            defphase(
                "Fix",
                stakes="high",
                body=[
                    bind(
                        "fixed",
                        loop(
                            max=3,
                            until="tests_pass",
                            body=[
                                bind("fix_gate", gate_bang(cmd="uv run pytest", workspace=base)),
                                valid_agent(
                                    "fixer",
                                    role="fixer",
                                    workspace=base,
                                    files={"doeff/fix.py"},
                                    prompt=prompt(
                                        "fix failures after ",
                                        ref("impls"),
                                        " from ",
                                        ref("fix_gate"),
                                    ),
                                ),
                            ],
                        ),
                    )
                ],
            ),
            defphase(
                "Tests",
                stakes="normal",
                body=[
                    bind(
                        "tests",
                        parallel(
                            valid_agent(
                                "unit-tests",
                                role="test_writer",
                                workspace=test_workspace,
                                files={"tests/test_unit.py"},
                                prompt=prompt("write unit tests for ", ref("fixed")),
                            ),
                            valid_agent(
                                "integration-tests",
                                role="test_writer",
                                workspace=test_workspace,
                                files={"tests/test_integration.py"},
                                prompt=prompt("write integration tests for ", ref("fixed")),
                            ),
                        ),
                    )
                ],
            ),
            defphase(
                "Gate",
                stakes="high",
                body=[
                    bind(
                        "gated",
                        loop(
                            max=3,
                            until="gate_passed",
                            body=[
                                bind("test_gate", gate_bang(cmd="uv run pytest", workspace=gate_workspace)),
                                valid_agent(
                                    "gate-fixer",
                                    role="fixer",
                                    workspace=gate_workspace,
                                    files={"doeff/gate_fix.py"},
                                    prompt=prompt(
                                        "repair gate failures after ",
                                        ref("tests"),
                                        " ",
                                        ref("test_gate"),
                                    ),
                                ),
                            ],
                        ),
                    )
                ],
            ),
            defphase(
                "Review",
                stakes="high",
                body=[
                    bind(
                        "reviews",
                        parallel(
                            *[
                                agent_bang(
                                    role="reviewer",
                                    verification_class="semantic",
                                    prompt=prompt("review axis ", axis, " for ", ref("gated")),
                                    schema=VERDICT_SCHEMA,
                                    workspace=gate_workspace,
                                    files={f"review/{axis}.md"},
                                    label=f"review-{axis}",
                                )
                                for axis in ["correctness", "tests", "architecture", "docs"]
                            ]
                        ),
                    ),
                    bind(
                        "merged",
                        merge_bang(
                            workspaces=[base, test_workspace, gate_workspace, *impl_workspaces],
                            strategy="merge",
                        ),
                    ),
                    artifact(prompt(ref("reviews"), ref("merged"))),
                ],
            ),
        ],
    )

    expanded = workflow.expand()

    assert len([node for node in expanded.nodes if node.kind == "agent"]) == 13
    assert len([node for node in expanded.nodes if node.kind == "gate"]) == 2
