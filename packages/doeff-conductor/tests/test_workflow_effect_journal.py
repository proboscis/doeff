"""Workflow effect journal regressions for explicit nondeterministic effects."""

from pathlib import Path

from doeff_conductor.api import ConductorAPI
from doeff_conductor.types import WorkflowStatus

RANDOM_WORKFLOW_SOURCE = """
(require doeff-hy.conductor [defworkflow random! <-])
(import doeff_conductor.dsl [artifact ref])

(defworkflow random-workflow
  :params {}
  :roles {}
  (<- value (random!))
  (artifact {"value" (ref "value")}))

(setv WORKFLOW random-workflow)
""".lstrip()


def test_random_effect_uses_entropy_for_new_runs_and_journal_for_replay(
    tmp_path: Path,
) -> None:
    workflow_path = tmp_path / "random_workflow.hy"
    workflow_path.write_text(RANDOM_WORKFLOW_SOURCE, encoding="utf-8")
    state_dir = tmp_path / "state"
    api = ConductorAPI(state_dir=state_dir)

    first = api.run_workflow(str(workflow_path), run_id="random-run-a")
    replayed = api.run_workflow(str(workflow_path), run_id="random-run-a")
    second = api.run_workflow(str(workflow_path), run_id="random-run-b")

    assert first.status == WorkflowStatus.DONE
    assert replayed.status == WorkflowStatus.DONE
    assert second.status == WorkflowStatus.DONE

    assert replayed.result_payload == first.result_payload
    assert second.result_payload != first.result_payload

    journal_path = state_dir / "workflows" / "random-run-a" / "effect-journal.jsonl"
    assert journal_path.exists()
    assert '"effect_kind":"random"' in journal_path.read_text(encoding="utf-8")
