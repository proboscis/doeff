"""Domain exceptions for doeff-git handlers."""


from dataclasses import dataclass
from subprocess import CalledProcessError


class GitError(Exception):
    """Base exception for doeff-git."""


@dataclass
class GitCommandError(GitError):
    """Raised when a git or gh command fails."""

    command: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    cwd: str | None = None

    def __post_init__(self) -> None:
        cmd_str = " ".join(self.command)
        parts = [f"Command failed: {cmd_str}", f"Exit code: {self.returncode}"]
        if self.cwd:
            parts.append(f"Working directory: {self.cwd}")
        if self.stderr:
            parts.append(f"stderr: {self.stderr}")
        if self.stdout:
            parts.append(f"stdout: {self.stdout}")
        super().__init__("\n".join(parts))

    @classmethod
    def from_subprocess_error(
        cls,
        error: CalledProcessError,
        *,
        cwd: str | None = None,
    ) -> "GitCommandError":
        command: list[str]
        if isinstance(error.cmd, (list, tuple)):
            command = [str(part) for part in error.cmd]
        else:
            command = [str(error.cmd)]

        stdout = error.stdout if isinstance(error.stdout, str) else ""
        stderr = error.stderr if isinstance(error.stderr, str) else ""
        return cls(
            command=command,
            returncode=error.returncode,
            stdout=stdout,
            stderr=stderr,
            cwd=cwd,
        )


__all__ = [
    "GitCommandError",
    "GitError",
]
