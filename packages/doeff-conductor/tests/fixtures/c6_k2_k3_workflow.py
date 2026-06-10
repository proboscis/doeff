"""C6 k2-k3-shaped workflow fixture for plan/validate CLI tests."""

from __future__ import annotations

from doeff_conductor.dsl import (
    agent_bang,
    artifact,
    bind,
    defphase,
    defworkflow,
    gate_bang,
    loop,
    merge_bang,
    parallel,
    prompt,
    ref,
    workspace_bang,
)

RESULT_SCHEMA = {
    "type": "object",
    "required": ["status"],
    "properties": {"status": {"type": "string"}},
}
VERDICT_SCHEMA = {
    "type": "object",
    "required": ["verdict", "findings"],
    "properties": {
        "verdict": {"enum": ["PASS", "CHANGES_REQUESTED"]},
        "findings": {"type": "array"},
    },
}


def _agent(label: str, role: str, workspace: object, **overrides: object) -> object:
    fields: dict[str, object] = {
        "role": role,
        "verification_class": "test-verifiable",
        "prompt": prompt("work on ", label),
        "schema": RESULT_SCHEMA,
        "workspace": workspace,
        "files": {f"{label}.py"},
        "label": label,
    }
    fields.update(overrides)
    return agent_bang(**fields)


base = workspace_bang(from_="main")
impl_workspaces = [workspace_bang(from_=f"main-impl-{index}") for index in range(5)]
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
                            _agent(
                                f"impl-{index}",
                                "implementer",
                                impl_workspaces[index],
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
                            _agent(
                                "fixer",
                                "fixer",
                                base,
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
                        _agent(
                            "unit-tests",
                            "test_writer",
                            test_workspace,
                            files={"tests/test_unit.py"},
                            prompt=prompt("write unit tests for ", ref("fixed")),
                        ),
                        _agent(
                            "integration-tests",
                            "test_writer",
                            test_workspace,
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
                            bind(
                                "test_gate",
                                gate_bang(cmd="uv run pytest", workspace=gate_workspace),
                            ),
                            _agent(
                                "gate-fixer",
                                "fixer",
                                gate_workspace,
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
