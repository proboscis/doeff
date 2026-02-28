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

from doeff_conductor import (
    Commit,
    CreateWorktree,
    DeleteWorktree,
    GitHandler,
    WorktreeEnv,
    WorktreeHandler,
    make_scheduled_handler,
)
from doeff_preset import preset_handlers

from doeff import Effect, EffectGenerator, Pass, default_handlers, do, run, slog


@do
def hello_workflow() -> EffectGenerator[str]:
    """A minimal workflow that creates a file and commits it.
    
    Returns:
        A success message.
    """
    # Step 1: Create a worktree
    env: WorktreeEnv = yield CreateWorktree(suffix="hello")
    yield slog(step="worktree", msg=f"Created worktree at: {env.path}")

    # Step 2: Make a change
    hello_file = env.path / "hello.txt"
    hello_file.write_text("Hello from doeff-conductor!\n")
    yield slog(step="file", msg=f"Created file: {hello_file}")

    # Step 3: Commit the change
    yield Commit(env=env, message="Add hello.txt")
    yield slog(step="commit", msg="Committed changes")

    # Step 4: Cleanup
    yield DeleteWorktree(env=env)
    yield slog(step="cleanup", msg="Cleaned up worktree")

    return "Hello workflow completed successfully!"


def main():
    """Run the hello workflow."""
    # Set up handlers
    worktree_handler = WorktreeHandler(repo_path=Path.cwd())
    git_handler = GitHandler()
    preset_handler = preset_handlers()
    create_worktree_handler = make_scheduled_handler(worktree_handler.handle_create_worktree)
    delete_worktree_handler = make_scheduled_handler(worktree_handler.handle_delete_worktree)
    commit_handler = make_scheduled_handler(git_handler.handle_commit)

    @do
    def workflow_handler(effect: Effect, k):
        if isinstance(effect, CreateWorktree):
            return (yield create_worktree_handler(effect, k))
        if isinstance(effect, DeleteWorktree):
            return (yield delete_worktree_handler(effect, k))
        if isinstance(effect, Commit):
            return (yield commit_handler(effect, k))
        yield Pass()

    # Run the workflow
    result = run(
        hello_workflow(),
        handlers=[preset_handler, workflow_handler, *default_handlers()],
    )

    print(f"\nResult: {result.value}")


if __name__ == "__main__":
    main()
