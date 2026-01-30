#!/usr/bin/env python
"""
Example 03: Merging Handlers

Demonstrates how to merge preset_handlers with domain-specific handlers
for use in real applications.

Run:
    cd packages/doeff-preset
    uv run python examples/03_merge_handlers.py
"""

from dataclasses import dataclass

from doeff import SyncRuntime, do
from doeff.cesk.frames import ContinueValue
from doeff.effects.base import EffectBase
from doeff.effects.writer import slog
from doeff_preset import preset_handlers


# Define a custom domain effect
@dataclass(frozen=True)
class FetchUserEffect(EffectBase):
    """Fetch user data by ID."""
    user_id: int


@dataclass(frozen=True)
class SendEmailEffect(EffectBase):
    """Send an email to a user."""
    to: str
    subject: str
    body: str


# Create mock handlers for our domain effects
def handle_fetch_user(effect, task_state, store):
    """Mock handler that returns fake user data."""
    users = {
        1: {"id": 1, "name": "Alice", "email": "alice@example.com"},
        2: {"id": 2, "name": "Bob", "email": "bob@example.com"},
    }
    user = users.get(effect.user_id)
    if user is None:
        raise ValueError(f"User {effect.user_id} not found")
    return ContinueValue(
        value=user,
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )


def handle_send_email(effect, task_state, store):
    """Mock handler that simulates sending an email."""
    print(f"  [Mock Email] To: {effect.to}, Subject: {effect.subject}")
    return ContinueValue(
        value={"sent": True, "to": effect.to},
        env=task_state.env,
        store=store,
        k=task_state.kontinuation,
    )


@do
def notification_workflow(user_id: int, message: str):
    """A workflow that uses both preset and domain effects."""
    yield slog(step="start", msg=f"Processing notification for user {user_id}")
    
    # Use domain effect
    user = yield FetchUserEffect(user_id=user_id)
    yield slog(step="user_fetched", name=user["name"], email=user["email"])
    
    # Send notification
    yield slog(step="sending", msg="Sending email notification")
    result = yield SendEmailEffect(
        to=user["email"],
        subject="Notification",
        body=message,
    )
    
    yield slog(step="done", msg="Notification sent", sent=result["sent"])
    return result


def main():
    """Run the merge handlers example."""
    print("=== Merging Handlers Example ===\n")
    
    # Create domain-specific handlers
    domain_handlers = {
        FetchUserEffect: handle_fetch_user,
        SendEmailEffect: handle_send_email,
    }
    
    # Merge preset handlers with domain handlers
    # Later handlers win on conflict (domain handlers override preset)
    handlers = {**preset_handlers(), **domain_handlers}
    
    runtime = SyncRuntime(handlers=handlers)
    result = runtime.run(notification_workflow(user_id=1, message="Hello from doeff!"))
    
    print(f"\n=== Results ===")
    print(f"Email sent: {result.value}")
    
    print("\n=== Alternative: Preset handlers win ===")
    # If you want preset handlers to win on conflict:
    handlers2 = {**domain_handlers, **preset_handlers()}
    # This reverses priority (not usually needed, but possible)


if __name__ == "__main__":
    main()
