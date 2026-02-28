"""Git handler for doeff-conductor.

This handler delegates git operations to doeff-git handlers while keeping
conductor effect APIs stable.
"""

from typing import TYPE_CHECKING

from doeff_git.effects import (
    CreatePR as GitCreatePR,
)
from doeff_git.effects import (
    GitCommit,
    GitPush,
)
from doeff_git.effects import (
    MergePR as GitMergePR,
)
from doeff_git.exceptions import GitCommandError as DomainGitCommandError
from doeff_git.handlers import GitHubHandler, GitLocalHandler
from doeff_git.types import PRHandle as GitPRHandle

from doeff_conductor.exceptions import GitCommandError

if TYPE_CHECKING:
    from doeff_conductor.effects.git import Commit, CreatePR, MergePR, Push
    from doeff_conductor.types import PRHandle


def _labels_to_list(labels: tuple[str, ...] | list[str] | None) -> list[str] | None:
    if labels is None:
        return None
    return list(labels)


def _strategy_to_value(strategy: object) -> str | None:
    if strategy is None:
        return None
    raw_value = getattr(strategy, "value", strategy)
    return str(raw_value)


class GitHandler:
    """Handler for conductor git effects via doeff-git delegates."""

    def __init__(
        self,
        *,
        local_handler: GitLocalHandler | None = None,
        github_handler: GitHubHandler | None = None,
    ) -> None:
        self._local_handler = local_handler or GitLocalHandler()
        self._github_handler = github_handler or GitHubHandler()

    @staticmethod
    def _translate_error(error: DomainGitCommandError) -> GitCommandError:
        return GitCommandError(
            command=error.command,
            returncode=error.returncode,
            stdout=error.stdout,
            stderr=error.stderr,
            cwd=error.cwd,
        )

    def handle_commit(self, effect: "Commit") -> str:
        """Stage changes and create a commit. Returns commit SHA."""
        git_effect = GitCommit(
            work_dir=effect.env.path,
            message=effect.message,
            all=effect.all,
        )
        try:
            return self._local_handler.handle_commit(git_effect)
        except DomainGitCommandError as error:
            raise self._translate_error(error) from error

    def handle_push(self, effect: "Push") -> None:
        """Push branch to remote. Raises GitCommandError on failure."""
        git_effect = GitPush(
            work_dir=effect.env.path,
            remote=effect.remote,
            force=effect.force,
            set_upstream=effect.set_upstream,
            branch=effect.env.branch,
        )
        try:
            self._local_handler.handle_push(git_effect)
        except DomainGitCommandError as error:
            raise self._translate_error(error) from error

    def handle_create_pr(self, effect: "CreatePR") -> "PRHandle":
        """Create a pull request using gh CLI."""
        from doeff_conductor.types import PRHandle

        git_effect = GitCreatePR(
            work_dir=effect.env.path,
            title=effect.title,
            body=effect.body,
            target=effect.target,
            draft=effect.draft,
            labels=_labels_to_list(effect.labels),
            head=effect.env.branch,
        )
        try:
            pr = self._github_handler.handle_create_pr(git_effect)
        except DomainGitCommandError as error:
            raise self._translate_error(error) from error

        return PRHandle(
            url=pr.url,
            number=pr.number,
            title=pr.title,
            branch=pr.branch,
            target=pr.target,
            status=pr.status,
            created_at=pr.created_at,
        )

    def handle_merge_pr(self, effect: "MergePR") -> None:
        """Merge a pull request using gh CLI. Raises GitCommandError on failure."""
        git_effect = GitMergePR(
            pr=GitPRHandle(
                url=effect.pr.url,
                number=effect.pr.number,
                title=effect.pr.title,
                branch=effect.pr.branch,
                target=effect.pr.target,
                status=effect.pr.status,
                created_at=effect.pr.created_at,
                work_dir=getattr(effect.pr, "work_dir", None),
            ),
            strategy=_strategy_to_value(effect.strategy),
            delete_branch=effect.delete_branch,
        )
        try:
            self._github_handler.handle_merge_pr(git_effect)
        except DomainGitCommandError as error:
            raise self._translate_error(error) from error


__all__ = ["GitHandler"]
