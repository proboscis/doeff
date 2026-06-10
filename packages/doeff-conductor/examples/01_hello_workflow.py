#!/usr/bin/env python
"""Example 01: minimal workspace workflow."""

from doeff import EffectGenerator, do

from doeff_conductor import Commit, CreateWorkspace, DeleteWorkspace, Exec, Workspace
from doeff_conductor.handlers import mock_handlers, run_sync


@do
def hello_workflow() -> EffectGenerator[str]:
    """Create a workspace, write a file through Exec, commit, and clean up."""
    workspace: Workspace = yield CreateWorkspace(suffix="hello")

    result = yield Exec(
        cmd="printf '%s\n' 'Hello from doeff-conductor!' > hello.txt",
        workspace=workspace,
        timeout=10,
    )
    if not result.passed:
        raise RuntimeError(f"file write failed; see {result.log_path}")

    sha = yield Commit(workspace=workspace, message="Add hello.txt")
    yield DeleteWorkspace(workspace=workspace)
    return f"Hello workflow completed on {workspace.ref} at {sha[:8]}"


def main() -> None:
    result = run_sync(hello_workflow(), scheduled_handlers=mock_handlers())
    if result.is_err():
        raise result.error
    print(result.value)


if __name__ == "__main__":
    main()
