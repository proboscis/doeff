#!/usr/bin/env python
"""
Example 08: Testing with effect-based mock handlers.

This example demonstrates a full effect flow without external services:
1. AgenticCreateSession
2. AgenticSendMessage
3. AgenticGetMessages

Run:
    uv run python packages/doeff-agentic/examples/08_testing_with_mocks.py
"""

from __future__ import annotations

import sys

from doeff import default_handlers, do, run
from doeff_agentic import (
    AgenticCreateSession,
    AgenticGetMessages,
    AgenticGetSessionStatus,
    AgenticSendMessage,
)
from doeff_agentic.handlers.testing import MockAgenticHandler, mock_handlers
from doeff_agentic.runtime import with_handler_map


@do
def mock_conversation():
    """Run a deterministic session using the testing handler."""
    session = yield AgenticCreateSession(name="reviewer", title="Mock Reviewer")
    yield AgenticSendMessage(
        session_id=session.id,
        content="Explain do-notation in one sentence.",
        wait=True,
    )
    messages = yield AgenticGetMessages(session_id=session.id)
    status = yield AgenticGetSessionStatus(session_id=session.id)
    return session.id, status.value, messages


def main() -> int:
    # runtime.with_handler_map composes typed handlers via WithHandler internally.
    handler_impl = MockAgenticHandler(workflow_name="mock-example")
    program = with_handler_map(mock_conversation(), mock_handlers(handler_impl))
    result = run(program, handlers=default_handlers())

    if result.is_err():
        print("Workflow failed")
        print(result.format())
        return 1

    session_id, status, messages = result.value
    assistant_messages = [m for m in messages if m.role == "assistant"]
    expected_response = "Mock response to: Explain do-notation in one sentence."

    if status != "done":
        print(f"Unexpected status: {status}")
        return 1
    if not assistant_messages:
        print("No assistant response found")
        return 1
    if assistant_messages[-1].content != expected_response:
        print("Unexpected assistant response")
        print(f"Expected: {expected_response}")
        print(f"Actual:   {assistant_messages[-1].content}")
        return 1

    print("SUCCESS: deterministic mock flow completed")
    print(f"Session: {session_id}")
    print(f"Status:  {status}")
    print(f"Reply:   {assistant_messages[-1].content}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
