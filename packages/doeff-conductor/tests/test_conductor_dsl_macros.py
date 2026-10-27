from __future__ import annotations

import subprocess
from pathlib import Path


def test_conductor_hy_macro_script_runs() -> None:
    script_path = Path(__file__).with_name("conductor_dsl_macro_script.hy")

    result = subprocess.run(
        ["hy", str(script_path)],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
