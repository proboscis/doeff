"""Git command helpers for conductor workspace materialization."""

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitCommandResult:
    """Captured result from a git command."""

    returncode: int
    stdout: str
    stderr: str


class GitCommandError(RuntimeError):
    """Raised when a git command exits unsuccessfully."""

    def __init__(self, args: list[str], *, cwd: Path, result: GitCommandResult) -> None:
        self.command_args = args
        self.cwd = cwd
        self.result = result
        super().__init__(
            f"git command failed in {cwd}: {' '.join(args)} "
            f"(exit {result.returncode})"
        )


def get_default_branch(repo_path: Path) -> str:
    """Get the default branch name for a repository."""
    try:
        result = run_git(["git", "symbolic-ref", "refs/remotes/origin/HEAD"], cwd=repo_path)
        return result.stdout.strip().split("/")[-1]
    except GitCommandError:
        for branch_name in ("main", "master"):
            result = run_git(
                ["git", "rev-parse", "--verify", f"refs/heads/{branch_name}"],
                cwd=repo_path,
                check=False,
            )
            if result.returncode == 0:
                return branch_name
        return "main"


def get_current_commit(repo_path: Path) -> str:
    """Get the current HEAD commit SHA for a repository."""
    result = run_git(["git", "rev-parse", "HEAD"], cwd=repo_path)
    return result.stdout.strip()


def get_repo_root(path: Path | None = None) -> Path:
    """Get the git repository root directory."""
    cwd: Path = path or Path.cwd()
    result = run_git(["git", "rev-parse", "--show-toplevel"], cwd=cwd)
    return Path(result.stdout.strip())


def run_git(
    args: list[str],
    *,
    cwd: Path,
    log_path: Path | None = None,
    check: bool = True,
) -> GitCommandResult:
    """Run a git command and optionally append full output to a log file."""
    completed = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    result = GitCommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    if log_path is not None:
        append_git_output(log_path, result)
    if check and result.returncode != 0:
        raise GitCommandError(args, cwd=cwd, result=result)
    return result


def append_git_output(log_path: Path, result: GitCommandResult) -> None:
    """Append captured git output to a log file."""
    with log_path.open("a", encoding="utf-8") as log_file:
        if result.stdout:
            log_file.write(result.stdout)
        if result.stderr:
            log_file.write(result.stderr)


def conflicted_files(path: Path) -> tuple[str, ...]:
    """Return the conflicted file list for a merge in progress."""
    result = run_git(
        ["git", "diff", "--name-only", "--diff-filter=U"],
        cwd=path,
        check=False,
    )
    return tuple(line for line in result.stdout.splitlines() if line)
