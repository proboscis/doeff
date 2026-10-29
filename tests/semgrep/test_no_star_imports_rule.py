"""doeff-no-star-imports の極性の検(card ki-3a06f37f859d)。

鳴らないべき形 = tools/hide_submodules.py の置く移し替えの module(module の子と package の子の 2 つの形 — ``if TYPE_CHECKING:``
の star import の後に ``sys.modules[__name__] = import_module(…)`` で自分を移した module に差し替える)。道具の ``shim_text`` が作る
text をそのまま当てるので、道具が形を変えて規則の外しから外れると、ここが赤になる。
鳴るべき形 = module の段の star import と、移し替えでない module の ``if TYPE_CHECKING:`` の中の star import。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from test_vm_failfast_semgrep_rules import _semgrep_results

pytestmark = pytest.mark.semgrep

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".semgrep.yaml"
RULE = "doeff-no-star-imports"
ROOT_NAME = "fixture_root"


def _hide_tool() -> ModuleType:
    """tools/ の道具を読む(tools/ は package ではない)。"""
    loaded = sys.modules.get("hide_submodules")
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(
        "hide_submodules", REPO_ROOT / "tools" / "hide_submodules.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["hide_submodules"] = module
    spec.loader.exec_module(module)
    return module


def test_the_moved_submodule_shims_do_not_fire_and_other_star_imports_do(tmp_path: Path) -> None:
    tool = _hide_tool()
    root_dir = tmp_path / "src" / ROOT_NAME
    (root_dir / "_handlers").mkdir(parents=True)  # package の子(移した後の dir)
    for name in ("effects", "handlers"):
        child = tool.Child(ROOT_NAME, name, root_dir)
        child.shim.write_text(tool.shim_text(child, True, {}), encoding="utf-8")
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
