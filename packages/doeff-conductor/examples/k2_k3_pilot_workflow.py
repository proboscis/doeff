"""C8 k2-k3 pilot workflow expressed as a DSL-only request artifact."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
from doeff_conductor.effects import REVIEW_VERDICT_RESULT_SCHEMA

IMPLEMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary", "changedFiles"],
    "properties": {
        "summary": {"type": "string"},
        "changedFiles": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
        "openQuestions": {"type": "string"},
    },
    "additionalProperties": False,
}

GATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["buildOk", "testOk", "lintOk", "summary"],
    "properties": {
        "buildOk": {"type": "boolean"},
        "testOk": {"type": "boolean"},
        "lintOk": {"type": "boolean"},
        "summary": {"type": "string"},
        "failures": {"type": "string"},
        "changedFiles": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}


@dataclass(frozen=True)
class PilotTask:
    """Static task descriptor shared by the DSL shape and production main."""

    node_id: str
    label: str
    suffix: str
    files: frozenset[str]
    prompt_text: str


IMPLEMENTER_TASKS: tuple[PilotTask, ...] = (
    PilotTask(
        node_id="impl-a-failure-kind",
        label="impl:A-failure-kind",
        suffix="impl-a",
        files=frozenset({"src/pilot_pkg/failure_kind.py"}),
        prompt_text=(
            "Task A: improve src/pilot_pkg/failure_kind.py only. Keep the conservative "
            "unknown-kind behavior, add a short module-level note that this mirrors the "
            "k2-k3 validation_failed lane, and return the required JSON artifact."
        ),
    ),
    PilotTask(
        node_id="impl-b-router",
        label="impl:B-router",
        suffix="impl-b",
        files=frozenset({"src/pilot_pkg/router.py"}),
        prompt_text=(
            "Task B: improve src/pilot_pkg/router.py only. Ensure both "
            "merge-agent-not-merged:* and merge-agent-validation-failed:* reasons route "
            "validation_failed to investigate and transient/stale kinds to retry."
        ),
    ),
    PilotTask(
        node_id="impl-c-gates",
        label="impl:C-gates",
        suffix="impl-c",
        files=frozenset({"src/pilot_pkg/gates.py"}),
        prompt_text=(
            "Task C: improve src/pilot_pkg/gates.py only. Keep the option ids exactly "
            "['rebase', 'fresh', 're-observe', 'cancel'] and document that each option "
            "closes over both the PR and the owning issue."
        ),
    ),
    PilotTask(
        node_id="impl-d-investigation",
        label="impl:D-investigation",
        suffix="impl-d",
        files=frozenset({"src/pilot_pkg/investigation.py"}),
        prompt_text=(
            "Task D: improve src/pilot_pkg/investigation.py only. Keep pr-code, mainline, "
            "control-plane-core, and transient-observation classifications explicit."
        ),
    ),
    PilotTask(
        node_id="impl-e-ownership",
        label="impl:E-ownership",
        suffix="impl-e",
        files=frozenset({"src/pilot_pkg/ownership.py"}),
        prompt_text=(
            "Task E: improve src/pilot_pkg/ownership.py only. Keep "
            "MergeValidationInvestigated owned by review-reconciler and add a small helper "
            "if useful. Do not edit any other file."
        ),
    ),
)

TEST_WRITER_TASKS: tuple[PilotTask, ...] = (
    PilotTask(
        node_id="test-routing",
        label="test:routing",
        suffix="tests-routing",
        files=frozenset({"tests/test_routing.py"}),
        prompt_text=(
            "Write tests/test_routing.py only. Cover validation_failed routing to "
            "investigate, agent_error routing to retry, and unknown legacy suffixes routing "
            "to investigate. Use unittest.TestCase so python -m unittest discover runs them."
        ),
    ),
    PilotTask(
        node_id="test-ownership",
        label="test:ownership",
        suffix="tests-ownership",
        files=frozenset({"tests/test_ownership.py"}),
        prompt_text=(
            "Write tests/test_ownership.py only. Cover the MergeValidationInvestigated "
            "owner and the merge exhausted gate option ids. Use unittest or "
            "unittest.TestCase so python -m unittest discover runs them."
        ),
    ),
)

REVIEW_AXES: tuple[tuple[str, str], ...] = (
    (
        "review-routing-closure",
        "Review route_failure and gate options for closure-law violations. PASS if no issue.",
    ),
    (
        "review-tests",
        "Review tests for coverage of validation_failed, agent_error, and ownership.",
    ),
    (
        "review-ownership",
        "Review CONDITION_OWNER consistency and single-writer assumptions.",
    ),
    (
        "review-known-blocker",
        "If docs/known_blocker.md exists, report one BLOCKER finding for that file.",
    ),
)

BUILD_COMMAND = "PYTHONPATH=src python3 tools/build_check.py"
TEST_COMMAND = "PYTHONPATH=src python3 -m unittest discover -s tests"
LINT_COMMAND = "PYTHONPATH=src python3 tools/lint_check.py"
FULL_GATE_COMMAND = f"{BUILD_COMMAND} && {TEST_COMMAND} && {LINT_COMMAND}"


def build_workflow():
    """Return the C7 DSL workflow shape used by plan/validate."""
    base_workspace = workspace_bang(from_="main")
    impl_workspaces = tuple(workspace_bang(from_=f"main-{task.suffix}") for task in IMPLEMENTER_TASKS)
    test_workspace = workspace_bang(from_="main-tests")
    gate_workspace = workspace_bang(from_="main-gate")

    return defworkflow(
        "k2_k3_pilot",
        params={"base_ref": str, "run_id": str},
        roles={
            "implementer": {"profile": "cheap-coder", "retry": 2},
            "fixer": {"profile": "cheap-coder", "retry": 2},
            "test-writer": {"profile": "cheap-coder", "retry": 2},
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
                                agent_bang(
                                    role="implementer",
                                    verification_class="test-verifiable",
                                    prompt=prompt(task.prompt_text),
                                    schema=IMPLEMENT_SCHEMA,
                                    workspace=impl_workspaces[index],
                                    files=task.files,
                                    label=task.label,
                                )
                                for index, task in enumerate(IMPLEMENTER_TASKS)
                            ]
                        ),
                    )
                ],
            ),
            defphase(
                "Reconcile",
                stakes="high",
                body=[
                    bind(
                        "reconciled",
                        loop(
                            max=3,
                            until="build_gate_passed",
                            body=[
                                bind("build_gate", gate_bang(cmd=BUILD_COMMAND, workspace=base_workspace)),
                                agent_bang(
                                    role="fixer",
                                    verification_class="test-verifiable",
                                    prompt=prompt(
                                        "fix compile/build failures after ",
                                        ref("impls"),
                                        " gate=",
                                        ref("build_gate"),
                                    ),
                                    schema=GATE_SCHEMA,
                                    workspace=base_workspace,
                                    files=frozenset({"src/pilot_pkg"}),
                                    label="fix:compile",
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
                            *[
                                agent_bang(
                                    role="test-writer",
                                    verification_class="test-verifiable",
                                    prompt=prompt(task.prompt_text, " after ", ref("reconciled")),
                                    schema=IMPLEMENT_SCHEMA,
                                    workspace=test_workspace,
                                    files=task.files,
                                    label=task.label,
                                )
                                for task in TEST_WRITER_TASKS
                            ]
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
                            until="full_gate_passed",
                            body=[
                                bind(
                                    "full_gate",
                                    gate_bang(cmd=FULL_GATE_COMMAND, workspace=gate_workspace),
                                ),
                                agent_bang(
                                    role="fixer",
                                    verification_class="test-verifiable",
                                    prompt=prompt(
                                        "fix gate failures after ",
                                        ref("tests"),
                                        " gate=",
                                        ref("full_gate"),
                                    ),
                                    schema=GATE_SCHEMA,
                                    workspace=gate_workspace,
                                    files=frozenset({"src/pilot_pkg/lint_sentinel.py"}),
                                    label="fix:gate",
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
                                    prompt=prompt(axis_prompt, " gated=", ref("gated")),
                                    schema=REVIEW_VERDICT_RESULT_SCHEMA,
                                    workspace=gate_workspace,
                                    files=frozenset({f"review/{axis}.md"}),
                                    label=axis,
                                )
                                for axis, axis_prompt in REVIEW_AXES
                            ]
                        ),
                    ),
                    bind(
                        "merged",
                        merge_bang(
                            workspaces=[
                                base_workspace,
                                test_workspace,
                                gate_workspace,
                                *impl_workspaces,
                            ],
                            strategy="merge",
                        ),
                    ),
                    artifact(prompt(ref("reviews"), ref("merged"))),
                ],
            ),
        ],
    )


WORKFLOW = build_workflow()
