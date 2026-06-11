"""Workspace identity is resume-stable across run_workflow invocations.

Spec §6/§9: for a given run id, every workspace-producing node — explicit
``workspace!`` and the implicit per-``agent!`` workspace — binds the same
workspace identity (branch + worktree) across process restarts. This is the
regression test for the 2026-06-11 false-positive-pipeline defect, where a
resumed run re-created fresh worktrees while agent sessions re-adopted their
deterministic names.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from doeff_conductor.api import ConductorAPI
from doeff_conductor.effects import CreateWorkspace
from doeff_conductor.handlers.testing import MockConductorRuntime, mock_handlers
from doeff_conductor.types import WorkflowStatus, Workspace

WORKFLOW_SOURCE = """
(require doeff-hy.conductor [defworkflow agent! workspace! <-])
(import doeff_conductor.dsl [artifact prompt ref])

(setv RESULT-SCHEMA {"type" "object"
                     "required" ["summary"]
                     "properties" {"summary" {"type" "string"}}
                     "additionalProperties" False})

(defworkflow resume-workflow
  :params {}
  :roles {"implementer" {"profile" "cheap-coder" "retry" 0}}
  (<- workspace (workspace! :from "main"))
  (<- explicit
      (agent! :role "implementer"
              :class "test-verifiable"
              :prompt (prompt "implement on the explicit workspace")
              :schema RESULT-SCHEMA
              :workspace (ref "workspace")
              :label "explicit"))
  (<- implicit
      (agent! :role "implementer"
              :class "test-verifiable"
              :prompt (prompt "implement on the implicit workspace")
              :schema RESULT-SCHEMA
              :label "implicit"))
  (artifact [(ref "explicit") (ref "implicit")]))

(setv WORKFLOW resume-workflow)
""".lstrip()


def test_same_run_id_binds_same_workspace_identity_for_explicit_and_implicit_nodes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_path = tmp_path / "resume_workflow.hy"
    workflow_path.write_text(WORKFLOW_SOURCE, encoding="utf-8")

    # One capture list per run_workflow invocation; each invocation gets a
    # FRESH mock runtime so nothing but the emitted identity can connect runs
    # (simulating a conductor process restart).
    captured_runs: list[list[tuple[str, Workspace]]] = []

    import doeff_conductor.handlers as handlers_module

    def production_handlers(**_: object):
        runtime = MockConductorRuntime(tmp_path / f"runtime-{len(captured_runs)}")
        run_capture: list[tuple[str, Workspace]] = []
        captured_runs.append(run_capture)

        def capture_create_workspace(effect: CreateWorkspace) -> Workspace:
            workspace = runtime.handle_create_workspace(effect)
            run_capture.append((effect.workspace_id, workspace))
            return workspace

        return mock_handlers(
            runtime=runtime,
            overrides={CreateWorkspace: capture_create_workspace},
        )

    monkeypatch.setattr(handlers_module, "production_handlers", production_handlers)

    api = ConductorAPI(state_dir=tmp_path / "state")
    first = api.run_workflow(str(workflow_path), run_id="resume-run")
    second = api.run_workflow(str(workflow_path), run_id="resume-run")

    assert first.status == WorkflowStatus.DONE
    assert second.status == WorkflowStatus.DONE
    assert len(captured_runs) == 2

    first_ids = [workspace_id for workspace_id, _ in captured_runs[0]]
    second_ids = [workspace_id for workspace_id, _ in captured_runs[1]]

    # One explicit workspace! node and one implicit per-agent workspace,
    # each with its own identity.
    assert len(first_ids) == 2
    assert len(set(first_ids)) == 2

    # The invariant: the same run id binds the same workspace identity for
    # every workspace-producing node, across invocations.
    assert second_ids == first_ids

    first_bindings = [(ws.id, ws.ref, ws.repo) for _, ws in captured_runs[0]]
    second_bindings = [(ws.id, ws.ref, ws.repo) for _, ws in captured_runs[1]]
    assert second_bindings == first_bindings


def test_different_run_id_binds_different_workspace_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_path = tmp_path / "resume_workflow.hy"
    workflow_path.write_text(WORKFLOW_SOURCE, encoding="utf-8")

    captured_runs: list[list[str]] = []

    import doeff_conductor.handlers as handlers_module

    def production_handlers(**_: object):
        runtime = MockConductorRuntime(tmp_path / f"runtime-{len(captured_runs)}")
        run_capture: list[str] = []
        captured_runs.append(run_capture)

        def capture_create_workspace(effect: CreateWorkspace) -> Workspace:
            run_capture.append(effect.workspace_id)
            return runtime.handle_create_workspace(effect)

        return mock_handlers(
            runtime=runtime,
            overrides={CreateWorkspace: capture_create_workspace},
        )

    monkeypatch.setattr(handlers_module, "production_handlers", production_handlers)

    api = ConductorAPI(state_dir=tmp_path / "state")
    api.run_workflow(str(workflow_path), run_id="run-one")
    api.run_workflow(str(workflow_path), run_id="run-two")

    assert len(captured_runs) == 2
    assert set(captured_runs[0]).isdisjoint(set(captured_runs[1]))


SHARED_SETV_SOURCE = """
(require doeff-hy.conductor [defworkflow agent! workspace! <-])
(import doeff_conductor.dsl [artifact prompt ref])

(setv RESULT-SCHEMA {"type" "object"
                     "required" ["summary"]
                     "properties" {"summary" {"type" "string"}}
                     "additionalProperties" False})

(setv ws (workspace! :from "main"))

(defworkflow shared-setv-workflow
  :params {}
  :roles {"implementer" {"profile" "cheap-coder" "retry" 0}}
  (<- a (agent! :role "implementer"
                :class "test-verifiable"
                :prompt (prompt "write on the shared workspace")
                :schema RESULT-SCHEMA
                :workspace ws
                :label "a"))
  (<- b (agent! :role "implementer"
                :class "test-verifiable"
                :prompt (prompt "gate on the same shared workspace")
                :schema RESULT-SCHEMA
                :workspace ws
                :label "b"))
  (artifact [(ref "a") (ref "b")]))

(setv WORKFLOW shared-setv-workflow)
""".lstrip()


def test_module_level_setv_workspace_is_one_workspace_and_resume_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity belongs to the workspace EXPRESSION, not the evaluation site.

    The dominant authoring pattern binds one module-level
    ``(setv ws (workspace! ...))`` and shares it across nodes; that is ONE
    workspace.  Keying identity by evaluation site instead silently gave
    every consumer a fresh worktree — so a gate tested a different tree
    than the implementer wrote to (caught live, 2026-06-11).
    """
    workflow_path = tmp_path / "shared_setv.hy"
    workflow_path.write_text(SHARED_SETV_SOURCE, encoding="utf-8")

    captured_runs: list[list[str]] = []

    import doeff_conductor.handlers as handlers_module

    def production_handlers(**_: object):
        runtime = MockConductorRuntime(tmp_path / f"runtime-{len(captured_runs)}")
        run_capture: list[str] = []
        captured_runs.append(run_capture)

        def capture_create_workspace(effect: CreateWorkspace) -> Workspace:
            run_capture.append(effect.workspace_id)
            return runtime.handle_create_workspace(effect)

        return mock_handlers(
            runtime=runtime,
            overrides={CreateWorkspace: capture_create_workspace},
        )

    monkeypatch.setattr(handlers_module, "production_handlers", production_handlers)

    api = ConductorAPI(state_dir=tmp_path / "state")
    first = api.run_workflow(str(workflow_path), run_id="shared-run")
    second = api.run_workflow(str(workflow_path), run_id="shared-run")

    assert first.status == WorkflowStatus.DONE
    assert second.status == WorkflowStatus.DONE

    # Both consumers materialize the SAME workspace exactly once.
    assert len(captured_runs[0]) == 1
    # ...and re-running the same run id re-binds the same identity.
    assert captured_runs[1] == captured_runs[0]
