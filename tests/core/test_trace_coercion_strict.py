from __future__ import annotations

import pytest

from doeff.trace import (
    ContextEntry,
    SpawnBoundary,
    coerce_active_chain_entries,
    coerce_active_chain_entry,
    coerce_trace_entry,
)


def test_coerce_active_chain_rejects_unknown_handler_status() -> None:
    with pytest.raises(ValueError, match="Unknown handler status"):
        coerce_active_chain_entry(
            {
                "kind": "effect_yield",
                "function_name": "f",
                "source_file": "file.py",
                "source_line": 1,
                "effect_repr": "Ask('x')",
                "handler_stack": [
                    {
                        "handler_name": "ReaderHandler",
                        "handler_kind": "python",
                        "source_file": "h.py",
                        "source_line": 10,
                        "status": "mystery_status",
                    }
                ],
                "result": {"kind": "active"},
            }
        )


def test_coerce_trace_entry_rejects_unknown_handler_kind() -> None:
    with pytest.raises(ValueError, match="Unknown handler kind"):
        coerce_trace_entry(
            {
                "kind": "dispatch",
                "dispatch_id": 1,
                "effect_repr": "Ask('x')",
                "handler_name": "ReaderHandler",
                "handler_kind": "mystery_kind",
                "delegation_chain": [],
                "action": "active",
            }
        )


def test_coerce_trace_entry_rejects_unknown_dispatch_action() -> None:
    with pytest.raises(ValueError, match="Unknown dispatch action"):
        coerce_trace_entry(
            {
                "kind": "dispatch",
                "dispatch_id": 1,
                "effect_repr": "Ask('x')",
                "handler_name": "ReaderHandler",
                "handler_kind": "python",
                "delegation_chain": [],
                "action": "mystery_action",
            }
        )


def test_coerce_active_chain_rejects_unknown_effect_result_kind() -> None:
    with pytest.raises(ValueError, match="Unknown effect result kind"):
        coerce_active_chain_entry(
            {
                "kind": "effect_yield",
                "function_name": "f",
                "source_file": "file.py",
                "source_line": 1,
                "effect_repr": "Ask('x')",
                "handler_stack": [],
                "result": {"kind": "mystery_result"},
            }
        )


def test_coerce_context_entry_wraps_unknown_payload() -> None:
    entry = coerce_active_chain_entry(
        {"kind": "context_entry", "data": {"kind": "custom", "value": 1}}
    )
    assert isinstance(entry, ContextEntry)
    assert entry.data == {"kind": "custom", "value": 1}


def test_coerce_active_chain_entries_promote_spawn_boundary_context_entries() -> None:
    entries = coerce_active_chain_entries(
        [
            {
                "kind": "program_yield",
                "function_name": "parent",
                "source_file": "parent.py",
                "source_line": 10,
                "sub_program_repr": "child()",
            },
            {
                "kind": "context_entry",
                "data": {
                    "kind": "spawn_boundary",
                    "task_id": 7,
                    "parent_task": 2,
                    "spawn_site": {
                        "function_name": "parent",
                        "source_file": "parent.py",
                        "source_line": 10,
                    },
                },
            },
            {
                "kind": "exception_site",
                "function_name": "child",
                "source_file": "child.py",
                "source_line": 20,
                "exception_type": "ValueError",
                "message": "boom",
            },
        ]
    )
    assert any(isinstance(entry, SpawnBoundary) for entry in entries)
