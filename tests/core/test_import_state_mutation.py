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


def test_shadowed_top_level_types_fails_instead_of_replacing_stdlib_module() -> None:
    outcome = _run_python(
        """
import json
import sys
from pathlib import Path

package_dir = str(Path.cwd() / "doeff")
sys.path.insert(0, package_dir)
sys.modules.pop("types", None)
try:
    import types
except BaseException as exc:
    print(json.dumps({
        "ok": False,
        "error_type": type(exc).__name__,
        "message": str(exc),
    }))
else:
    print(json.dumps({
        "ok": True,
        "module_file": getattr(types, "__file__", None),
        "module_name": types.__name__,
    }))
"""
    )
    assert outcome["ok"] is False
    assert outcome["error_type"] in {"ImportError", "ModuleNotFoundError", "RuntimeError"}
    assert "types" in str(outcome["message"])


def test_importing_doeff_types_does_not_replace_sys_modules_types() -> None:
    outcome = _run_python(
        """
import json
import sys

stdlib_types = sys.modules["types"]
import doeff.types
current_types = sys.modules["types"]
print(json.dumps({
    "same_object": stdlib_types is current_types,
    "module_name": current_types.__name__,
}))
"""
    )
    assert outcome["same_object"] is True
    assert outcome["module_name"] == "types"
