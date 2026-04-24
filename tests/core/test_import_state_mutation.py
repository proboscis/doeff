from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VM_PYTHONPATH = str(ROOT / "packages" / "doeff-vm")


def _run_python(script: str) -> dict[str, object]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        VM_PYTHONPATH if not existing else f"{VM_PYTHONPATH}{os.pathsep}{existing}"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def test_importing_doeff_does_not_rewrite_sys_path() -> None:
    outcome = _run_python(
        """
import json
import sys
from pathlib import Path

package_dir = str(Path.cwd() / "doeff")
sys.path.insert(0, package_dir)
before = list(sys.path)
import doeff
after = list(sys.path)
print(json.dumps({"before": before, "after": after, "package_dir": package_dir}))
"""
    )
    assert outcome["after"] == outcome["before"]
    assert outcome["package_dir"] in outcome["after"]



