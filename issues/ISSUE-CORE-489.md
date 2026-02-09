# ISSUE-CORE-489: orch run pipeline smoke-test completion

## Summary

This issue validates that the orch run pipeline can start an agent and complete a trivial task.

## Acceptance Criteria

- [x] Agent boots successfully
- [x] Agent can read this issue
- [x] Agent responds with confirmation that it understands the task
- [x] Agent marks the task complete

## Validation Evidence

- `uv run python -c "print('agent_boot_ok')"` -> `agent_boot_ok`
- `uv run python -c "from pathlib import Path; text = Path('VAULT/issues/ISSUE-CORE-489.md').read_text(); print('issue_read_ok' if 'ISSUE-CORE-489' in text else 'issue_read_fail')"` -> `issue_read_ok`
- `uv run python -c "from pathlib import Path; text = Path('VAULT/issues/ISSUE-CORE-489.md').read_text(); checks = ['- [x] Agent boots successfully', '- [x] Agent can read this issue', '- [x] Agent responds with confirmation that it understands the task', '- [x] Agent marks the task complete']; print('task_marked_complete_ok' if 'status: completed' in text and all(c in text for c in checks) else 'task_marked_complete_fail')"` -> `task_marked_complete_ok`
- `uv run pytest -q` -> fails during collection with 13 pre-existing `ModuleNotFoundError: No module named 'doeff.cesk'` errors
- `uv run pytest tests/public_api/test_types_001_handler_protocol.py -q` -> `13 passed in 0.02s`

## Completion

Task is marked complete in `VAULT/issues/ISSUE-CORE-489.md` by setting `status: completed` and checking all acceptance boxes.
