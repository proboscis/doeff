"""Create the scratch repository used by the C7 k2-k3 pilot workflow."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        nargs="?",
        default="/tmp/doeff-conductor-k2-k3-scratch",
        help="Scratch repository path. The directory is recreated.",
    )
    args: argparse.Namespace = parser.parse_args()
    repo_path: Path = Path(args.path).expanduser().resolve()

    if repo_path.exists():
        shutil.rmtree(repo_path)
    _write_scratch_repo(repo_path)
    _run(["git", "init", "-b", "main"], cwd=repo_path)
    _run(["git", "config", "user.email", "c7-pilot@example.invalid"], cwd=repo_path)
    _run(["git", "config", "user.name", "C7 Pilot"], cwd=repo_path)
    _run(["git", "add", "."], cwd=repo_path)
    _run(["git", "commit", "-m", "seed c7 k2-k3 scratch repo"], cwd=repo_path)
    print(repo_path)


def _write_scratch_repo(repo_path: Path) -> None:
    package_path: Path = repo_path / "src" / "pilot_pkg"
    tests_path: Path = repo_path / "tests"
    tools_path: Path = repo_path / "tools"
    docs_path: Path = repo_path / "docs"
    for directory_path in (package_path, tests_path, tools_path, docs_path):
        directory_path.mkdir(parents=True, exist_ok=True)

    (repo_path / "README.md").write_text(
        "# C7 k2-k3 scratch repo\n\n"
        "Synthetic target for the doeff-conductor C7 pilot. The gates are:\n"
        "build: `python3 tools/build_check.py`\n"
        "test: `PYTHONPATH=src python3 -m unittest discover -s tests`\n"
        "lint: `python3 tools/lint_check.py`\n",
        encoding="utf-8",
    )
    (package_path / "__init__.py").write_text(
        '"""Synthetic review-routing package for the C7 pilot."""\n',
        encoding="utf-8",
    )
    (package_path / "failure_kind.py").write_text(
        '''"""Failure-kind parsing helpers."""

from __future__ import annotations


VALID_FAILURE_KINDS = {
    "none",
    "stale_head",
    "stale_base",
    "validation_failed",
    "agent_error",
}


def parse_failure_kind(value: str) -> str:
    """Return a normalized failure kind or the conservative validation lane."""
    normalized = value.strip().lower().replace("-", "_")
    if normalized in VALID_FAILURE_KINDS:
        return normalized
    return "validation_failed"


def render_failure_kind(value: str) -> str:
    """Render a failure kind after validating it."""
    parsed = parse_failure_kind(value)
    if parsed != value:
        raise ValueError(f"unknown failure kind: {value}")
    return parsed
''',
        encoding="utf-8",
    )
    (package_path / "router.py").write_text(
        '''"""Merge-failure routing policy."""

from __future__ import annotations

from pilot_pkg.failure_kind import parse_failure_kind


def route_failure(reason: str) -> str:
    """Route a merge failure reason into retry, investigate, or pass."""
    if reason == "merge-agent-stale-head":
        return "retry"
    if reason.startswith("merge-agent-not-merged:"):
        kind = parse_failure_kind(reason.split(":", 1)[1])
        if kind == "validation_failed":
            return "investigate"
        if kind in {"stale_head", "stale_base", "agent_error"}:
            return "retry"
    return "investigate"
''',
        encoding="utf-8",
    )
    (package_path / "gates.py").write_text(
        '''"""Closure-preserving gate options."""

from __future__ import annotations


def merge_exhausted_options() -> list[str]:
    """Return closure-preserving options for a merge-exhausted gate."""
    return ["rebase", "fresh", "re-observe", "cancel"]
''',
        encoding="utf-8",
    )
    (package_path / "investigation.py").write_text(
        '''"""Investigation result classification."""

from __future__ import annotations


def classify_evidence(evidence: str) -> str:
    """Classify validation evidence by owner."""
    lowered = evidence.lower()
    if "mainline" in lowered:
        return "mainline"
    if "control-plane" in lowered:
        return "control-plane-core"
    if "network" in lowered:
        return "transient-observation"
    return "pr-code"
''',
        encoding="utf-8",
    )
    (package_path / "ownership.py").write_text(
        '''"""Condition ownership table."""

from __future__ import annotations


CONDITION_OWNER = {
    "MergeAttempts": "review-reconciler",
    "MergeValidationInvestigated": "review-reconciler",
    "Implemented": "impl-reconciler",
}


def owner_for(condition_type: str) -> str:
    """Return the owner for a durable condition type."""
    return CONDITION_OWNER[condition_type]
''',
        encoding="utf-8",
    )
    (package_path / "lint_sentinel.py").write_text(
        '''"""Gate-loop sentinel that the gate fixer must remove."""

C7_BLOCKER_LINT_SENTINEL = True
''',
        encoding="utf-8",
    )
    (docs_path / "known_blocker.md").write_text(
        "# Known pilot blocker\n\n"
        "The pilot intentionally leaves this document in place so one adversarial "
        "reviewer can emit a BLOCKER finding and exercise tier-2 routing.\n",
        encoding="utf-8",
    )
    (tests_path / "test_seed.py").write_text(
        '''"""Seed tests that pass before workflow-added tests arrive."""

from __future__ import annotations

import unittest

from pilot_pkg.failure_kind import parse_failure_kind


class FailureKindSeedTest(unittest.TestCase):
    def test_unknown_failure_kind_is_validation_failed(self) -> None:
        self.assertEqual(parse_failure_kind("semgrep_failed"), "validation_failed")
''',
        encoding="utf-8",
    )
    (tools_path / "build_check.py").write_text(
        '''"""Build gate for the C7 pilot scratch repo."""

from __future__ import annotations

import compileall
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    ok = compileall.compile_dir(root / "src", quiet=1)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
''',
        encoding="utf-8",
    )
    (tools_path / "lint_check.py").write_text(
        '''"""Lint gate for the C7 pilot scratch repo."""

from __future__ import annotations

from pathlib import Path


SENTINEL = "C7_BLOCKER_LINT_SENTINEL"


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    findings = []
    for path in sorted((root / "src").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if SENTINEL in text:
            findings.append(path.relative_to(root).as_posix())
    if findings:
        print("lint sentinel remains in: " + ", ".join(findings))
        return 1
    print("lint ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
        encoding="utf-8",
    )


def _run(args: list[str], *, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True)


if __name__ == "__main__":
    main()
