#!/usr/bin/env python
"""
Example 01: Hello Workflow

Demonstrates a minimal doeff-conductor workflow that:
1. Creates a worktree
2. Makes a simple change (writes a file)
3. Commits and cleans up

Run:
    cd packages/doeff-conductor
    uv run python examples/01_hello_workflow.py
"""

from pathlib import Path
from typing import Any, Callable

from doeff import do, EffectGenerator, SyncRuntime
from doeff.runtime import Resume
from doeff_conductor import (
    CreateWorktree,
    DeleteWorktree,
    Commit,
    WorktreeHandler,
    GitHandler,
    WorktreeEnv,
)


def sync_handler(fn: Callable[[Any], Any]) -> Callable:
    """Wrap a simple handler function to match SyncRuntime's expected signature.
    
    SyncRuntime handlers must accept (effect, env, store) and return Resume(value, store).
    This helper wraps a function that just takes the effect and returns the value.
    """
    def handler(effect: Any, env: Any, store: Any):
        result = fn(effect)
        return Resume(result, store)
    return handler


@do
def hello_workflow() -> EffectGenerator[str]:
    """A minimal workflow that creates a file and commits it.
    
    Returns:
        A success message.
    """
    # Step 1: Create a worktree
    env: WorktreeEnv = yield CreateWorktree(suffix="hello")
    print(f"Created worktree at: {env.path}")
    
    # Step 2: Make a change
    hello_file = env.path / "hello.txt"
    hello_file.write_text("Hello from doeff-conductor!\n")
    print(f"Created file: {hello_file}")
    
    # Step 3: Commit the change
    yield Commit(env=env, message="Add hello.txt")
    print("Committed changes")
    
    # Step 4: Cleanup
    yield DeleteWorktree(env=env)
    print("Cleaned up worktree")
    
    return "Hello workflow completed successfully!"


def main():
    """Run the hello workflow."""
    # Set up handlers
    worktree_handler = WorktreeHandler(repo_path=Path.cwd())
    git_handler = GitHandler()
    
    handlers = {
        CreateWorktree: sync_handler(worktree_handler.handle_create_worktree),
        DeleteWorktree: sync_handler(worktree_handler.handle_delete_worktree),
        Commit: sync_handler(git_handler.handle_commit),
    }
    
    # Run the workflow
    runtime = SyncRuntime(handlers=handlers)
    result = runtime.run(hello_workflow())
    
    print(f"\nResult: {result}")


if __name__ == "__main__":
    main()
