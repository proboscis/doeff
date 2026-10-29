"""Production handlers for doeff-git effects."""


from doeff import K, Pass, Resume, handler, stacked_handlers
from doeff.program import ProgramHandler
from doeff_git import _commands
from doeff_git._effects import CreatePR, GitCommit, GitDiff, GitPull, GitPush, MergePR


@handler
def git_local_handler(effect: GitCommit | GitDiff | GitPush | GitPull, k: K):
    """Answer the local git effects (``GitCommit``, ``GitDiff``, ``GitPush``, ``GitPull``)
    by running the git CLI in the effect's ``work_dir``; pass every other effect on.

    A failed command raises ``GitCommandError`` in the program that yielded the effect.
    """
    if isinstance(effect, GitCommit):
        return (yield Resume(k, _commands.commit(effect)))
    if isinstance(effect, GitDiff):
        return (yield Resume(k, _commands.diff(effect)))
    if isinstance(effect, GitPush):
        return (yield Resume(k, _commands.push(effect)))
    if isinstance(effect, GitPull):
        return (yield Resume(k, _commands.pull(effect)))
    return (yield Pass(effect, k))


@handler
def github_handler(effect: CreatePR | MergePR, k: K):
    """Answer the hosting effects (``CreatePR``, ``MergePR``) by running the GitHub CLI
    (``gh``); pass every other effect on.

    A failed command raises ``GitCommandError`` in the program that yielded the effect.
    """
    if isinstance(effect, CreatePR):
        return (yield Resume(k, _commands.create_pr(effect)))
    if isinstance(effect, MergePR):
        return (yield Resume(k, _commands.merge_pr(effect)))
    return (yield Pass(effect, k))


def production_handlers() -> ProgramHandler:
    """Install ``git_local_handler`` and ``github_handler`` as one handler: git CLI for the
    local effects, GitHub CLI for the hosting effects.

    To answer some effects another way, install that handler inside this one (nearer the
    program); the effects it passes on reach these.
    """
    return stacked_handlers(github_handler, git_local_handler)


__all__ = [
    "git_local_handler",
    "github_handler",
    "production_handlers",
]
