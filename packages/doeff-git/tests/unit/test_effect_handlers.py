# ruff: noqa: E402
"""Tests for doeff-git effects and handlers."""


import importlib
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from doeff import K, Pass, Resume, do, handler, run

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

import doeff_git
from doeff_git.effects import CreatePR, GitCommit, GitDiff, GitPull, GitPush, MergePR
from doeff_git.handlers import (
    MockGitRuntime,
    git_local_handler,
    github_handler,
    mock_handlers,
    production_handlers,
)
from doeff_git.types import MergeStrategy, PRHandle


@do
def _perform(effect):
    return (yield effect)


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
    assert handlers_module.git_local_handler is git_local_handler
    assert handlers_module.github_handler is github_handler
    assert handlers_module.mock_handlers is mock_handlers
    assert doeff_git.git_local_handler is git_local_handler
    assert doeff_git.github_handler is github_handler


def test_handlers_are_doeff_handlers_not_classes() -> None:
    # ADR-DOE-API-001 R4: the handlers are (effect, k) handlers; the classes are gone.
    for name in ("GitLocalHandler", "GitHubHandler"):
        assert not hasattr(doeff_git, name)
        assert not hasattr(importlib.import_module("doeff_git.handlers"), name)
    for installer in (git_local_handler, github_handler, production_handlers()):
        assert installer._doeff_is_handler_fn is True


def test_mock_handlers_run_program() -> None:
    runtime = MockGitRuntime()
    sha, pr = run(mock_handlers(runtime=runtime)(_mock_flow(Path("/tmp/mock-repo"))))

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

    (repo_path / "feature.txt").write_text("line1\n")
    sha = run(
        git_local_handler(
            _perform(GitCommit(work_dir=repo_path, message="feat: add feature", all=True))
        )
    )

    assert len(sha) == 40

    (repo_path / "feature.txt").write_text("line1\nline2\n")
    diff = run(git_local_handler(_perform(GitDiff(work_dir=repo_path))))
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

    @do
    def push_then_pull():
        pushed = yield GitPush(work_dir=repo_path, set_upstream=True, branch="main")
        pulled = yield GitPull(work_dir=repo_path, remote="origin", branch="main")
        return pushed, pulled

    assert run(git_local_handler(push_then_pull())) == (None, None)

    result = subprocess.run(
        ["git", "ls-remote", str(remote_path)],
        text=True,
        check=True,
        capture_output=True,
    )
    assert "refs/heads/main" in result.stdout


def test_local_handler_raises_git_command_error_in_the_program(tmp_path: Path) -> None:
    @do
    def commit_outside_a_repo():
        try:
            yield GitCommit(work_dir=tmp_path, message="no repo", all=False)
        except doeff_git.GitCommandError as error:
            return error.command
        return None

    assert run(git_local_handler(commit_outside_a_repo())) == [
        "git",
        "commit",
        "-m",
        "no repo",
    ]


@patch("doeff_git._commands._run_command")
def test_github_handler_create_pr_composes_command(mock_run: MagicMock) -> None:
    mock_run.side_effect = [
        MagicMock(stdout="feature-branch\n", returncode=0),
        MagicMock(stdout="https://github.com/acme/repo/pull/42\n", returncode=0),
    ]

    pr = run(
        github_handler(
            _perform(
                CreatePR(
                    work_dir=Path("/tmp/repo"),
                    title="Add feature",
                    body="Body",
                    target="main",
                    draft=True,
                    labels=["feature", "ready"],
                )
            )
        )
    )

    create_args = mock_run.call_args_list[1].args[0]
    assert create_args[-5:] == ["--draft", "--label", "feature", "--label", "ready"]
    assert pr.number == 42
    assert pr.branch == "feature-branch"


@patch("doeff_git._commands._run_command")
def test_github_handler_merge_pr_uses_strategy(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(stdout="", returncode=0)

    pr = PRHandle(
        url="https://github.com/acme/repo/pull/7",
        number=7,
        title="Test PR",
        branch="feature",
        target="main",
    )
    run(
        github_handler(
            _perform(MergePR(pr=pr, strategy=MergeStrategy.SQUASH, delete_branch=True))
        )
    )

    args = mock_run.call_args.args[0]
    assert args == ["gh", "pr", "merge", pr.url, "--squash", "--delete-branch"]


@handler
def _commit_stub(effect: GitCommit, k: K):
    if isinstance(effect, GitCommit):
        return (yield Resume(k, f"sha:{effect.message}"))
    return (yield Pass(effect, k))


@patch("doeff_git._commands._run_command")
def test_a_handler_installed_inside_production_handlers_answers_first(
    mock_run: MagicMock,
) -> None:
    # GitCommit is answered by the stub nearer the program; CreatePR passes the stub and
    # git_local_handler on to github_handler.
    mock_run.return_value = MagicMock(stdout="https://example.test/pull/1\n", returncode=0)

    @do
    def flow():
        sha = yield GitCommit(work_dir=Path("/tmp/repo"), message="feat")
        pr = yield CreatePR(work_dir=Path("/tmp/repo"), title="Title", head="stub-branch")
        return sha, pr

    sha, pr = run(production_handlers()(_commit_stub(flow())))

    assert sha == "sha:feat"
    assert pr.number == 1
    assert [call.args[0][:3] for call in mock_run.call_args_list] == [["gh", "pr", "create"]]
