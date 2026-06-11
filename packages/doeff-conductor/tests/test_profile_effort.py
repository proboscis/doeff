"""Effort is an axis of the profile binding (L0 identity), never a run parameter."""

from __future__ import annotations

from pathlib import Path

import pytest
from doeff_conductor.api import ConductorAPI
from doeff_conductor.effects import AgentEffect, AgentTask
from doeff_conductor.environment import DEFAULT_PROFILE_DATA, ProfileBinding
from doeff_conductor.handlers.testing import MockConductorRuntime, mock_handlers
from doeff_conductor.types import WorkflowStatus

BASE_PROFILE = {
    "adapter": "codex",
    "model": None,
    "capabilities": ("durable-sessions",),
    "budget_units": 1,
}


class TestProfileBindingEffortValidation:
    def test_from_mapping_accepts_non_empty_effort(self) -> None:
        profile = ProfileBinding.from_mapping("p", {**BASE_PROFILE, "effort": "xhigh"})
        assert profile.effort == "xhigh"

    def test_from_mapping_defaults_effort_to_none(self) -> None:
        profile = ProfileBinding.from_mapping("p", BASE_PROFILE)
        assert profile.effort is None

    def test_from_mapping_accepts_explicit_none_effort(self) -> None:
        profile = ProfileBinding.from_mapping("p", {**BASE_PROFILE, "effort": None})
        assert profile.effort is None

    def test_from_mapping_rejects_empty_effort(self) -> None:
        with pytest.raises(ValueError, match="effort must be a non-empty string or None"):
            ProfileBinding.from_mapping("p", {**BASE_PROFILE, "effort": ""})

    def test_from_mapping_rejects_non_string_effort(self) -> None:
        with pytest.raises(ValueError, match="effort must be a non-empty string or None"):
            ProfileBinding.from_mapping("p", {**BASE_PROFILE, "effort": 3})

    def test_resolved_identity_carries_effort(self) -> None:
        profile = ProfileBinding.from_mapping("p", {**BASE_PROFILE, "effort": "xhigh"})
        assert profile.resolved_identity.effort == "xhigh"

    def test_identity_fingerprint_distinguishes_effort(self) -> None:
        xhigh = ProfileBinding.from_mapping("p", {**BASE_PROFILE, "effort": "xhigh"})
        low = ProfileBinding.from_mapping("p", {**BASE_PROFILE, "effort": "low"})
        assert xhigh.identity_fingerprint != low.identity_fingerprint


def test_default_profiles_all_run_at_xhigh() -> None:
    """House policy: every default profile runs at xhigh."""
    assert DEFAULT_PROFILE_DATA
    for name, data in DEFAULT_PROFILE_DATA.items():
        assert data["effort"] == "xhigh", f"profile {name!r} must default to xhigh effort"


def test_workflow_runtime_passes_profile_effort_into_agent_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The AgentTask effort comes from the resolved profile, never from params."""
    workflow_path = tmp_path / "effort_workflow.hy"
    workflow_path.write_text(
        """
(require doeff-hy.conductor [defworkflow agent! workspace! <-])
(import doeff_conductor.dsl [artifact prompt ref])

(setv RESULT-SCHEMA {"type" "object"
                     "required" ["summary"]
                     "properties" {"summary" {"type" "string"}}
                     "additionalProperties" False})

(defworkflow effort-workflow
  :params {}
  :roles {"implementer" {"profile" "cheap-coder" "retry" 0}}
  (<- workspace (workspace! :from "main"))
  (<- result
      (agent! :role "implementer"
              :class "test-verifiable"
              :prompt (prompt "implement")
              :schema RESULT-SCHEMA
              :workspace (ref "workspace")
              :label "implement"))
  (artifact (ref "result")))

(setv WORKFLOW effort-workflow)
""".lstrip(),
        encoding="utf-8",
    )

    runtime = MockConductorRuntime(tmp_path / "runtime")
    captured_tasks: list[AgentTask] = []

    def capture_agent(effect: AgentEffect) -> object:
        captured_tasks.append(effect.task)
        return runtime.handle_agent(effect)

    import doeff_conductor.handlers as handlers_module

    def production_handlers(**_: object):
        return mock_handlers(runtime=runtime, overrides={AgentEffect: capture_agent})

    monkeypatch.setattr(handlers_module, "production_handlers", production_handlers)

    api = ConductorAPI(state_dir=tmp_path / "state")
    handle = api.run_workflow(str(workflow_path), run_id="effort-run")

    assert handle.status == WorkflowStatus.DONE
    assert len(captured_tasks) == 1
    task = captured_tasks[0]
    # The default cheap-coder profile binds effort=xhigh (house policy).
    assert task.effort == "xhigh"
    assert task.resolved_identity is not None
    assert task.resolved_identity.effort == "xhigh"


def test_session_id_carries_resolved_identity_fingerprint() -> None:
    """A profile edit must yield a NEW session, not re-adopt the old one.

    The journal invalidates on fingerprint change (new generation), but
    session launch is idempotent by name — without the fingerprint in the
    name, the re-dispatched agent re-adopted the stale DONE session and
    returned its old payload (observed live), defeating D7 end to end.
    """
    from doeff_conductor.effects import AgentTask
    from doeff_conductor.replay_keying import ResolvedIdentity

    def task(effort: str) -> AgentTask:
        return AgentTask(
            run_id="run-1",
            node_id="wf/0/agent",
            attempt=0,
            env=None,  # type: ignore[arg-type]
            prompt="p",
            result_schema={},
            verification_class="mechanical",
            agent_type="codex",
            resolved_identity=ResolvedIdentity(
                adapter="codex", model="gpt-5", identity=None, effort=effort
            ),
        )

    assert task("xhigh").session_id == task("xhigh").session_id
    assert task("xhigh").session_id != task("high").session_id
