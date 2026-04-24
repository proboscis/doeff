from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _import_error_with_blocked_doeff_vm(module_name: str) -> dict[str, str]:
    script = f"""
import importlib.abc
import json
import sys

sys.path.insert(0, {str(ROOT)!r})

class _BlockDoeffVm(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "doeff_vm" or fullname.startswith("doeff_vm."):
            raise ModuleNotFoundError("blocked doeff_vm for test")
        return None

sys.meta_path.insert(0, _BlockDoeffVm())
try:
    __import__({module_name!r})
except BaseException as exc:
    print(json.dumps({{"type": type(exc).__name__, "message": str(exc)}}))
    raise SystemExit(0)
raise SystemExit("import unexpectedly succeeded")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def test_importing_top_level_doeff_fails_when_doeff_vm_is_unavailable() -> None:
    error = _import_error_with_blocked_doeff_vm("doeff")
    assert error["type"] in {"ModuleNotFoundError", "ImportError"}
    assert "doeff_vm" in error["message"]


def test_importing_program_module_fails_when_doeff_vm_is_unavailable() -> None:
    error = _import_error_with_blocked_doeff_vm("doeff.program")
    assert error["type"] in {"ModuleNotFoundError", "ImportError"}
    assert "doeff_vm" in error["message"]


def test_importing_types_internal_fails_when_doeff_vm_is_unavailable() -> None:
    error = _import_error_with_blocked_doeff_vm("doeff._types_internal")
    assert error["type"] in {"ModuleNotFoundError", "ImportError"}
    assert "doeff_vm" in error["message"]


def test_importing_errors_module_fails_when_doeff_vm_is_unavailable() -> None:
    error = _import_error_with_blocked_doeff_vm("doeff.errors")
    assert error["type"] in {"ModuleNotFoundError", "ImportError"}
    assert "doeff_vm" in error["message"]


