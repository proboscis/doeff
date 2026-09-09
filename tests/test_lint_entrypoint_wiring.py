"""既存lint入口の欠測拒否と、Python/Hy差分のpre-commit選択を実行して検証する。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT: Path = Path(__file__).resolve().parents[1]


def _tool(directory: Path, name: str, exit_code: int) -> None:
    path: Path = directory / name
    path.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['LINT_CALLS'], 'a') as output:\n"
        "    output.write(json.dumps([os.path.basename(sys.argv[0]), *sys.argv[1:]]) + '\\n')\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _environment(directory: Path) -> dict[str, str]:
    environment: dict[str, str] = dict(os.environ)
    environment.update({
        "PATH": f"{directory}:/usr/bin:/bin",
        "LINT_CALLS": str(directory / "calls.jsonl"),
        "PRE_COMMIT_HOME": str(directory / "pre-commit-cache"),
        "PRE_COMMIT_ALLOW_NO_CONFIG": "0",
    })
    environment.pop("SKIP", None)
    return environment


@pytest.mark.parametrize("tool_status", [None, 0, 1, 2])
def test_make_lint_doeff_propagates_missing_and_tool_failure(
    tmp_path: Path, tool_status: int | None,
) -> None:
    if tool_status is not None:
        _tool(tmp_path, "doeff-linter", tool_status)
    result: subprocess.CompletedProcess[str] = subprocess.run(
        ["/usr/bin/make", "-f", str(ROOT / "Makefile"), "lint-doeff"],
        cwd=tmp_path, env=_environment(tmp_path), capture_output=True, text=True, check=False,
    )
    assert (result.returncode == 0) == (tool_status == 0), result.stdout + result.stderr
    if tool_status is None:
        assert "doeff-linter" in result.stderr
        assert not (tmp_path / "calls.jsonl").exists()
    else:
        assert json.loads((tmp_path / "calls.jsonl").read_text()) == [
            "doeff-linter", "--no-log", "doeff/", "packages/",
        ]


def _repository(directory: Path, changed: str, source: str) -> Path:
    repository: Path = directory / "repository"
    repository.mkdir()
    subprocess.run(["/usr/bin/git", "init", "-q", str(repository)], check=True)
    shutil.copyfile(ROOT / ".pre-commit-config.yaml", repository / ".pre-commit-config.yaml")
    changed_path: Path = repository / changed
    changed_path.parent.mkdir(parents=True, exist_ok=True)
    changed_path.write_text(source)
    subprocess.run(["/usr/bin/git", "add", "."], cwd=repository, check=True)
    return repository


def _pre_commit(
    directory: Path, changed: str, *, semgrep_status: int | None = 0,
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    _tool(directory, "doeff-linter", 0)
    if semgrep_status is not None:
        _tool(directory, "semgrep", semgrep_status)
    repository: Path = _repository(directory, changed, "value = 1\n")
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [sys.executable, "-m", "pre_commit", "run", "--files", changed],
        cwd=repository, env=_environment(directory), capture_output=True, text=True, check=False,
    )
    calls_path: Path = directory / "calls.jsonl"
    calls: list[list[str]] = (
        [json.loads(line) for line in calls_path.read_text().splitlines()]
        if calls_path.exists() else []
    )
    return result, calls


@pytest.mark.parametrize("changed", [
    "doeff/example.py", "packages/example.hy", "packages/example.hyk", "packages/example.hyp",
])
def test_pre_commit_runs_matching_linter_on_only_the_changed_file(
    tmp_path: Path, changed: str,
) -> None:
    result, calls = _pre_commit(tmp_path, changed)
    assert result.returncode == 0, result.stdout + result.stderr
    semgrep_calls: list[list[str]] = [call for call in calls if call[0] == "semgrep"]
    assert len(semgrep_calls) == 1, result.stdout + result.stderr
    assert semgrep_calls[0][-1] == changed
    assert "--error" in semgrep_calls[0]
    assert "doeff/" not in semgrep_calls[0]
    assert "packages/" not in semgrep_calls[0]
    python_calls: list[list[str]] = [call for call in calls if call[0] == "doeff-linter"]
    assert len(python_calls) == int(changed.endswith(".py"))


@pytest.mark.parametrize("tool_status", [None, 1, 2])
def test_hy_only_change_rejects_missing_or_failing_semgrep(
    tmp_path: Path, tool_status: int | None,
) -> None:
    result, calls = _pre_commit(tmp_path, "packages/example.hy", semgrep_status=tool_status)
    assert result.returncode != 0, result.stdout + result.stderr
    assert not any(call[0] == "doeff-linter" for call in calls)


def test_unrelated_document_does_not_require_python_or_hy_linters(tmp_path: Path) -> None:
    result, calls = _pre_commit(tmp_path, "notes.md", semgrep_status=None)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == []


@pytest.mark.parametrize("nested", [False, True])
def test_hy_only_hook_runs_real_semgrep_handler_boundary_rule(
    tmp_path: Path, nested: bool,
) -> None:
    executable: str | None = shutil.which("semgrep")
    assert executable is not None, "実検体の検査には dev 依存の semgrep が必要です"
    (tmp_path / "semgrep").symlink_to(executable)
    changed: str = "packages/example/src/handlers/example.hy"
    source: str = (
        "(defn factory []\n  (defhandler nested [] (object [] (resume None))))\n"
        if nested else "(defhandler top-level [] (object [] (resume None)))\n"
    )
    repository: Path = _repository(tmp_path, changed, source)
    rule_id: str = "doeff-hy-defhandler-must-be-top-level"
    rules = yaml.safe_load((ROOT / ".semgrep.yaml").read_text())["rules"]
    selected = [rule for rule in rules if rule["id"] == rule_id]
    assert len(selected) == 1
    (repository / ".semgrep.yaml").write_text(yaml.safe_dump({"rules": selected}))
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [sys.executable, "-m", "pre_commit", "run", "semgrep-hy", "--files", changed],
        cwd=repository, env=_environment(tmp_path), capture_output=True, text=True, check=False,
    )
    assert result.returncode == int(nested), result.stdout + result.stderr
    if nested:
        assert rule_id in result.stdout + result.stderr
