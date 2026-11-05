from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VM_PYTHONPATH = str(ROOT / "packages" / "doeff-vm")


def _run_python(script: str) -> dict[str, object]:
    """Run the script in a separate Python and read the JSON printed last. The child inherits the environment (not copied -- the environment is not read),
    and the command adds doeff-vm's source location to the head of the import path (#2896)."""
    prelude = f"import sys; sys.path.insert(0, {VM_PYTHONPATH!r})\n"
    result = subprocess.run(
        [sys.executable, "-c", prelude + script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
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
