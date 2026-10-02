from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VM_PYTHONPATH = str(ROOT / "packages" / "doeff-vm")


def _run_python(script: str) -> dict[str, object]:
    """script を別の Python で走らせ、最後に print した JSON を読むため。子は環境を継ぎ(写さない — 環境を読まない)、
    doeff-vm の source の置き場を import の道の先頭に、命令の中で足す(#2896)。"""
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
