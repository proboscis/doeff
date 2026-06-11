import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_make_lint_runs_root_ruff_target() -> None:
    result = subprocess.run(
        ["make", "-n", "lint"],
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=True,
    )

    output = result.stdout + result.stderr

    assert "uv run ruff check doeff/ tests/ packages/" in output
