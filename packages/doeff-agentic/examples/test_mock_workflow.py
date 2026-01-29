#!/usr/bin/env python
"""
Test Mock Workflow Patterns (No Dependencies Required)

This script demonstrates workflow patterns using mock data,
without requiring OpenCode or any external services.

It shows how to:
1. Create mock session and message objects
2. Simulate workflow logic
3. Test conditional branching based on outputs

Run:
    cd packages/doeff-agentic
    uv run python examples/test_mock_workflow.py
"""

import sys
from datetime import datetime, timezone

from doeff_agentic import (
    AgenticMessage,
    AgenticSessionHandle,
    AgenticSessionStatus,
)


class MockAgentSimulator:
    """Simulates agent responses for testing workflow logic."""

    def __init__(self):
        self.sessions: dict[str, AgenticSessionHandle] = {}
        self.messages: dict[str, list[AgenticMessage]] = {}
        self._msg_counter = 0
        self._sess_counter = 0

    def create_session(self, name: str, title: str | None = None) -> AgenticSessionHandle:
        """Create a mock session."""
        self._sess_counter += 1
        session = AgenticSessionHandle(
            id=f"mock-sess-{self._sess_counter}",
            name=name,
            workflow_id="mock-workflow",
            environment_id="mock-env",
            status=AgenticSessionStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
            title=title or name,
        )
        self.sessions[name] = session
        self.messages[session.id] = []
        return session

    def send_message(self, session_id: str, content: str) -> AgenticMessage:
        """Send a message and get a simulated response."""
        # Add user message
        self._msg_counter += 1
        user_msg = AgenticMessage(
            id=f"msg-{self._msg_counter}",
            session_id=session_id,
            role="user",
            content=content,
            created_at=datetime.now(timezone.utc),
        )
        self.messages[session_id].append(user_msg)

        # Generate contextual mock response
        self._msg_counter += 1
        response = self._generate_response(content)
        assistant_msg = AgenticMessage(
            id=f"msg-{self._msg_counter}",
            session_id=session_id,
            role="assistant",
            content=response,
            created_at=datetime.now(timezone.utc),
        )
        self.messages[session_id].append(assistant_msg)

        return assistant_msg

    def _generate_response(self, content: str) -> str:
        """Generate a contextual mock response."""
        content_lower = content.lower()

        if "review" in content_lower and "code" in content_lower:
            # Randomly decide if issues found (for demo, always finds issues)
            return (
                "I've reviewed the code and found the following issues:\n"
                "- Line 15: Missing error handling for empty input\n"
                "- Line 42: Potential division by zero\n"
                "- Line 67: Unused variable 'temp'"
            )
        if "lgtm" in content_lower or "looks good" in content_lower:
            return "LGTM - No issues found. The code is clean and well-structured."
        if "fix" in content_lower:
            return (
                "I've applied the following fixes:\n"
                "- Added null check for empty input\n"
                "- Added guard for division by zero\n"
                "- Removed unused variable"
            )
        if "summarize" in content_lower:
            return "Summary: The implementation follows best practices with proper error handling."
        if "research" in content_lower:
            return (
                "Research findings:\n"
                "1. Functional programming emphasizes immutability\n"
                "2. Pure functions have no side effects\n"
                "3. Higher-order functions enable composition"
            )
        return f"I understand. Processing: {content[:50]}..."

    def get_messages(self, session_id: str) -> list[AgenticMessage]:
        """Get all messages for a session."""
        return self.messages.get(session_id, [])

    def get_last_assistant_message(self, session_id: str) -> str:
        """Get the last assistant message content."""
        messages = self.get_messages(session_id)
        for msg in reversed(messages):
            if msg.role == "assistant":
                return msg.content
        return ""


def test_simple_workflow():
    """Test a simple single-agent workflow."""
    print("Test 1: Simple Workflow")

    sim = MockAgentSimulator()

    # Create session
    session = sim.create_session("greeter", "Greeting Agent")
    print(f"  Created session: {session.name} (id={session.id})")

    # Send message
    response = sim.send_message(session.id, "Hello, how are you?")
    print(f"  Response: {response.content[:50]}...")

    # Verify
    messages = sim.get_messages(session.id)
    if len(messages) == 2 and messages[0].role == "user" and messages[1].role == "assistant":
        print("  ✓ Simple workflow passed")
        return True
    print("  ✗ Simple workflow failed")
    return False


def test_sequential_workflow():
    """Test a sequential two-agent workflow (research → summarize)."""
    print("\nTest 2: Sequential Workflow")

    sim = MockAgentSimulator()

    # First agent: researcher
    researcher = sim.create_session("researcher", "Research Agent")
    sim.send_message(researcher.id, "Research the topic of functional programming")
    research = sim.get_last_assistant_message(researcher.id)
    print(f"  Research: {research[:50]}...")

    # Second agent: summarizer (uses research output)
    summarizer = sim.create_session("summarizer", "Summary Agent")
    sim.send_message(summarizer.id, f"Summarize this research:\n{research}")
    summary = sim.get_last_assistant_message(summarizer.id)
    print(f"  Summary: {summary[:50]}...")

    # Verify
    if research and summary and len(sim.sessions) == 2:
        print("  ✓ Sequential workflow passed")
        return True
    print("  ✗ Sequential workflow failed")
    return False


def test_conditional_workflow():
    """Test a conditional workflow (review → maybe fix)."""
    print("\nTest 3: Conditional Workflow")

    sim = MockAgentSimulator()

    # Reviewer agent
    reviewer = sim.create_session("reviewer", "Code Reviewer")
    sim.send_message(reviewer.id, "Review this code for issues")
    review = sim.get_last_assistant_message(reviewer.id)
    print(f"  Review: {review[:50]}...")

    # Check if issues found (conditional branch)
    has_issues = "LGTM" not in review.upper()
    print(f"  Issues found: {has_issues}")

    if has_issues:
        # Create fixer agent
        fixer = sim.create_session("fixer", "Code Fixer")
        sim.send_message(fixer.id, f"Fix these issues:\n{review}")
        fixes = sim.get_last_assistant_message(fixer.id)
        print(f"  Fixes: {fixes[:50]}...")
        result = {"status": "fixed", "review": review, "fixes": fixes}
    else:
        result = {"status": "approved", "review": review}

    # Verify
    if result["status"] in ("approved", "fixed"):
        print(f"  Result status: {result['status']}")
        print("  ✓ Conditional workflow passed")
        return True
    print("  ✗ Conditional workflow failed")
    return False


def test_parallel_simulation():
    """Test simulating parallel agent execution."""
    print("\nTest 4: Parallel Workflow Simulation")

    sim = MockAgentSimulator()

    # Create multiple sessions (simulating parallel creation)
    tech = sim.create_session("tech-analyst", "Technical Analyst")
    biz = sim.create_session("biz-analyst", "Business Analyst")
    ux = sim.create_session("ux-analyst", "UX Analyst")
    print(f"  Created {len(sim.sessions)} parallel sessions")

    # Send messages to all (simulating parallel execution)
    topic = "AI code assistants"
    sim.send_message(tech.id, f"Analyze {topic} from technical perspective")
    sim.send_message(biz.id, f"Analyze {topic} from business perspective")
    sim.send_message(ux.id, f"Analyze {topic} from UX perspective")

    # Collect results
    perspectives = {
        "technical": sim.get_last_assistant_message(tech.id),
        "business": sim.get_last_assistant_message(biz.id),
        "ux": sim.get_last_assistant_message(ux.id),
    }

    for name, content in perspectives.items():
        print(f"  {name}: {content[:40]}...")

    # Verify
    if all(perspectives.values()):
        print("  ✓ Parallel workflow simulation passed")
        return True
    print("  ✗ Parallel workflow simulation failed")
    return False


def main():
    """Run all workflow pattern tests."""
    print("=" * 60)
    print("Testing Workflow Patterns with Mock Simulator")
    print("=" * 60)
    print()
    print("This demonstrates workflow patterns without requiring")
    print("OpenCode or any external services.")
    print()

    results = [
        test_simple_workflow(),
        test_sequential_workflow(),
        test_conditional_workflow(),
        test_parallel_simulation(),
    ]

    print()
    print("=" * 60)
    passed = sum(results)
    total = len(results)

    if passed == total:
        print(f"SUCCESS: All {total} tests passed!")
        return 0
    print(f"FAILED: {passed}/{total} tests passed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
