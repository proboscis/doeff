"""Git handler for doeff-conductor.

``git_handler`` answers the conductor git effects by performing the doeff-git effects
(``GitCommit``, ``GitPush``, ``CreatePR``, ``MergePR``) for the workspace's worktree, so a
doeff-git handler installed outside it (``doeff_git.production_handlers``) runs
git and gh. The worktree path comes from the workspace handler (``WorkspacePath``).
"""

from collections.abc import Generator
from pathlib import Path
from typing import Any

from doeff_git import CreatePR as GitCreatePR
from doeff_git import GitCommandError as DomainGitCommandError
from doeff_git import GitCommit, GitPush
from doeff_git import MergePR as GitMergePR
from doeff_git import PRHandle as GitPRHandle

from doeff import EffectBase, K, Resume, do
from doeff.program import FormerFactoryHandler, _former_factory_handler
from doeff_conductor._effects.git import Commit, CreatePR, MergePR, Push
from doeff_conductor._exceptions import GitCommandError
from doeff_conductor._git_workspace import get_current_commit, run_git
from doeff_conductor._handlers._workspace_path import WorkspacePath
from doeff_conductor._types import MergeStrategy, PRHandle

__all__ = ["git_handler"]


def _labels_to_list(labels: tuple[str, ...] | list[str] | None) -> list[str] | None:
    if labels is None:
        return None
    return list(labels)


def _strategy_to_value(strategy: object) -> str | None:
    if strategy is None:
        return None
    if isinstance(strategy, MergeStrategy):
        return strategy.value
    return str(strategy)


def _translate_error(error: DomainGitCommandError) -> GitCommandError:
    return GitCommandError(
        command=error.command,
        returncode=error.returncode,
        stdout=error.stdout,
        stderr=error.stderr,
        cwd=error.cwd,
    )


@do
def _perform_git(git_effect: EffectBase) -> Generator[EffectBase, Any, Any]:
    """Perform one doeff-git effect outward, raising its failure as the conductor's GitCommandError."""
    try:
        return (yield git_effect)
    except DomainGitCommandError as error:
        raise _translate_error(error) from error


@do
def _commit(effect: Commit) -> Generator[EffectBase, Any, str]:
    """Stage changes and create a commit. Returns commit SHA."""
    work_dir: Path = yield WorkspacePath(workspace=effect.workspace)
    if effect.skip_if_clean:
        status = run_git(["git", "status", "--porcelain"], cwd=work_dir)
        if not status.stdout.strip():
            return get_current_commit(work_dir)
    return (
        yield _perform_git(
            GitCommit(
                work_dir=work_dir,
                message=effect.message,
                all=effect.all,
            )
        )
    )


@do
def _push(effect: Push) -> Generator[EffectBase, Any, None]:
    """Push branch to remote. Raises GitCommandError on failure."""
    work_dir: Path = yield WorkspacePath(workspace=effect.workspace)
    yield _perform_git(
        GitPush(
            work_dir=work_dir,
            remote=effect.remote,
            force=effect.force,
            set_upstream=effect.set_upstream,
            branch=effect.workspace.ref,
        )
    )


@do
def _create_pr(effect: CreatePR) -> Generator[EffectBase, Any, PRHandle]:
    """Create a pull request from the workspace branch."""
    work_dir: Path = yield WorkspacePath(workspace=effect.workspace)
    pr: GitPRHandle = yield _perform_git(
        GitCreatePR(
            work_dir=work_dir,
            title=effect.title,
            body=effect.body,
            target=effect.target,
            draft=effect.draft,
            labels=_labels_to_list(effect.labels),
            head=effect.workspace.ref,
        )
    )
    return PRHandle(
        url=pr.url,
        number=pr.number,
        title=pr.title,
        branch=pr.branch,
        target=pr.target,
        status=pr.status,
        created_at=pr.created_at,
    )


@do
def _merge_pr(effect: MergePR) -> Generator[EffectBase, Any, None]:
    """Merge a pull request. Raises GitCommandError on failure."""
    yield _perform_git(
        GitMergePR(
            pr=GitPRHandle(
                url=effect.pr.url,
                number=effect.pr.number,
                title=effect.pr.title,
                branch=effect.pr.branch,
                target=effect.pr.target,
                status=effect.pr.status,
                created_at=effect.pr.created_at,
                work_dir=None,
            ),
            strategy=_strategy_to_value(effect.strategy),
            delete_branch=effect.delete_branch,
        )
    )


def _answer_git(effect: Commit | Push | CreatePR | MergePR, k: K):
    """Answer one conductor git effect through doeff-git."""
    match effect:
        case Commit():
            answer: object = yield _commit(effect)
        case Push():
            answer = yield _push(effect)
        case CreatePR():
            answer = yield _create_pr(effect)
        case MergePR():
            answer = yield _merge_pr(effect)
    return (yield Resume(k, answer))


git_handler: FormerFactoryHandler = _former_factory_handler(_answer_git, "git_handler")
"""The handler for conductor git effects.

A handler value — ``git_handler(program)`` or ``[..., git_handler]`` (ADR-DOE-API-002); the former
``git_handler()`` answers it with a DeprecationWarning for one release.

It answers ``Commit``, ``Push``, ``CreatePR`` and ``MergePR`` by performing the doeff-git effects,
so it is installed inside a doeff-git handler and inside the workspace handler. A failed git or gh
command raises the conductor's ``GitCommandError``.
"""
