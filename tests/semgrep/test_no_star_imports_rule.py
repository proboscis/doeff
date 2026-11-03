"""doeff-no-star-imports の極性の検。

鳴らないべき形 = 移し替えの module(module の子と package の子の 2 つの形 — ``if TYPE_CHECKING:``
の star import の後に ``sys.modules[__name__] = import_module(…)`` で自分を移した module に差し替える)。この test の ``_shim_text`` が書く形を当てる。
鳴るべき形 = module の段の star import と、移し替えでない module の ``if TYPE_CHECKING:`` の中の star import。
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
    """移し替えの module の形(削除した the former move tool の道具が置いた text と同じ形)。"""
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
    (root_dir / "_handlers").mkdir(parents=True)  # package の子(移した後の dir)
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
