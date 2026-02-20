# ruff: noqa: E402
"""Tests for doeff-git effects and handlers."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from doeff import do
from doeff.rust_vm import run_with_handler_map

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from doeff_git.effects import CreatePR, GitCommit, GitDiff, GitPull, GitPush, MergePR
from doeff_git.handlers import (
    GitHubHandler,
    GitLocalHandler,
    MockGitRuntime,
    mock_handlers,
    production_handlers,
)
from doeff_git.types import MergeStrategy, PRHandle


@do
def _mock_flow(work_dir: Path):
    sha = yield GitCommit(work_dir=work_dir, message="feat: add tests")
    _ = yield GitPush(work_dir=work_dir)
    pr = yield CreatePR(work_dir=work_dir, title="Add tests")
    _ = yield MergePR(pr=pr)
    return sha, pr


def test_effect_exports() -> None:
    effects_module = importlib.import_module("doeff_git.effects")
    assert effects_module.GitCommit is GitCommit
    assert effects_module.GitPush is GitPush
    assert effects_module.CreatePR is CreatePR


def test_handler_exports() -> None:
    handlers_module = importlib.import_module("doeff_git.handlers")
    assert handlers_module.GitLocalHandler is GitLocalHandler
    assert handlers_module.GitHubHandler is GitHubHandler
    assert handlers_module.mock_handlers is mock_handlers


def test_mock_handlers_run_program() -> None:
    runtime = MockGitRuntime()
    result = run_with_handler_map(
        _mock_flow(Path("/tmp/mock-repo")),
        mock_handlers(runtime=runtime),
    )

    assert result.is_ok()
    sha, pr = result.value
    assert len(sha) == 40
    assert runtime.pushes
    assert pr.number == 1
    assert runtime.prs[1].status == "merged"


def _init_test_repo(repo_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    (repo_path / "README.md").write_text("# Repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"], cwd=repo_path, check=True, capture_output=True
    )


def test_local_handler_commit_and_diff(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    _init_test_repo(repo_path)

    handler = GitLocalHandler()

    (repo_path / "feature.txt").write_text("line1\n")
    sha = handler.handle_commit(
        GitCommit(work_dir=repo_path, message="feat: add feature", all=True)
    )

    assert len(sha) == 40

    (repo_path / "feature.txt").write_text("line1\nline2\n")
    diff = handler.handle_diff(GitDiff(work_dir=repo_path))
    assert "line2" in diff


def test_local_handler_push_and_pull(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    _init_test_repo(repo_path)

    remote_path = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", str(remote_path)],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )

    handler = GitLocalHandler()
    handler.handle_push(GitPush(work_dir=repo_path, set_upstream=True, branch="main"))
    handler.handle_pull(GitPull(work_dir=repo_path, remote="origin", branch="main"))

    result = subprocess.run(
        ["git", "ls-remote", str(remote_path)],
        text=True,
        check=True,
        capture_output=True,
    )
    assert "refs/heads/main" in result.stdout


@patch("doeff_git.handlers.production._run_command")
def test_github_handler_create_pr_composes_command(mock_run: MagicMock) -> None:
    mock_run.side_effect = [
        MagicMock(stdout="feature-branch\n", returncode=0),
        MagicMock(stdout="https://github.com/acme/repo/pull/42\n", returncode=0),
    ]

    handler = GitHubHandler()
    pr = handler.handle_create_pr(
        CreatePR(
            work_dir=Path("/tmp/repo"),
            title="Add feature",
            body="Body",
            target="main",
            draft=True,
            labels=["feature", "ready"],
        )
    )

    create_args = mock_run.call_args_list[1].args[0]
    assert "--draft" in create_args
    assert "--label" in create_args
    assert pr.number == 42
    assert pr.branch == "feature-branch"


@patch("doeff_git.handlers.production._run_command")
def test_github_handler_merge_pr_uses_strategy(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(stdout="", returncode=0)

    handler = GitHubHandler()
    pr = PRHandle(
        url="https://github.com/acme/repo/pull/7",
        number=7,
        title="Test PR",
        branch="feature",
        target="main",
    )
    handler.handle_merge_pr(MergePR(pr=pr, strategy=MergeStrategy.SQUASH, delete_branch=True))

    args = mock_run.call_args.args[0]
    assert "--squash" in args
    assert "--delete-branch" in args


def test_production_handlers_support_handler_swapping() -> None:
    class LocalStub:
        def handle_commit(self, effect: GitCommit) -> str:
            return f"sha:{effect.message}"

        def handle_diff(self, _effect: GitDiff) -> str:
            return "diff"

        def handle_push(self, _effect: GitPush) -> None:
            return None

        def handle_pull(self, _effect: GitPull) -> None:
            return None

    class HostingStub:
        def handle_create_pr(self, effect: CreatePR) -> PRHandle:
            return PRHandle(
                url="https://example.test/pull/1",
                number=1,
                title=effect.title,
                branch="stub-branch",
                target=effect.target,
            )

        def handle_merge_pr(self, _effect: MergePR) -> None:
            return None

    @do
    def flow():
        sha = yield GitCommit(work_dir=Path("/tmp/repo"), message="feat")
        pr = yield CreatePR(work_dir=Path("/tmp/repo"), title="Title")
        return sha, pr

    result = run_with_handler_map(
        flow(),
        production_handlers(local_handler=LocalStub(), github_handler=HostingStub()),
    )

    assert result.is_ok()
    sha, pr = result.value
    assert sha == "sha:feat"
    assert pr.number == 1
