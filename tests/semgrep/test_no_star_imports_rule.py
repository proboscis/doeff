"""Polarity test of doeff-no-star-imports.

Forms that should not fire = a shim module (two forms: a module's child and a package's child -- after a star import under ``if TYPE_CHECKING:``,
it replaces itself with the module moved to by ``sys.modules[__name__] = import_module(...)``). This test's ``_shim_text`` writes the form to match.
Forms that should fire = a module-level star import, and a star import inside ``if TYPE_CHECKING:`` in a module that is not a shim.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_vm_failfast_semgrep_rules import _semgrep_results

pytestmark = pytest.mark.semgrep

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".semgrep.yaml"
RULE = "doeff-no-star-imports"
ROOT_NAME = "fixture_root"


def _shim_text(old: str, new: str) -> str:
    """The shape of a shim module (the same shape as the text placed by the removed former move tool)."""
    return (
        f"# moved-submodule-shim: {old} -> {new}\n"
        "import sys\n"
        "from importlib import import_module\n"
        "from typing import TYPE_CHECKING\n"
        "\n"
        "if TYPE_CHECKING:\n"
        f"    from {new} import *  # noqa: F403  # nosemgrep: doeff-no-star-imports\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    import runpy\n"
        "\n"
        f'    runpy.run_module("{new}", run_name="__main__", alter_sys=True)\n'
        "else:\n"
        f'    sys.modules[__name__] = import_module("{new}")\n'
    )


def test_the_moved_submodule_shims_do_not_fire_and_other_star_imports_do(tmp_path: Path) -> None:
    root_dir = tmp_path / "src" / ROOT_NAME
    (root_dir / "_handlers").mkdir(parents=True)  # a package's child (the dir after the move)
    for name in ("effects", "handlers"):
        (root_dir / f"{name}.py").write_text(
            _shim_text(f"{ROOT_NAME}.{name}", f"{ROOT_NAME}._{name}"), encoding="utf-8"
        )
    plain = tmp_path / "src" / "plain.py"
    plain.write_text(
        "from typing import TYPE_CHECKING\n"
        "\n"
        "from os.path import *\n"
        "\n"
        "if TYPE_CHECKING:\n"
        "    from collections import *\n",
        encoding="utf-8",
    )
    findings = _semgrep_results(CONFIG, "src", cwd=tmp_path, project_root=tmp_path)
    hits = sorted(
        (Path(finding["path"]).as_posix(), finding["start"]["line"])
        for finding in findings
        if finding["check_id"].endswith(RULE)
    )
    assert hits == [("src/plain.py", 3), ("src/plain.py", 6)]
