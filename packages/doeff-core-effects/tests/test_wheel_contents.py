"""Check that the wheel built from doeff-core-effects contains all the top-level packages of this distribution.

An editable environment (the development venv) only adds the package dirs to sys.path, so even a package whose name is missing from the distribution settings (packages in pyproject.toml
[tool.hatch.build.targets.wheel]) can be imported. The case where it is left out of the wheel only and the import fails in production (the worker installed from the wheel) is
named here by building with a real uv build -- if doeff_lifeline is missing, the worker shim does not start and no job starts.
"""

import subprocess
import zipfile
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_the_built_wheel_has_every_top_level_package(tmp_path: Path) -> None:
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path), str(PACKAGE_ROOT)],
        check=True,
        capture_output=True,
        timeout=60,
    )
    (wheel,) = tmp_path.glob("*.whl")
    tops = {name.split("/", 1)[0] for name in zipfile.ZipFile(wheel).namelist() if "/" in name}
    sources = {path.parent.name for path in PACKAGE_ROOT.glob("*/__init__.py")}
    assert sources <= tops, f"top-level packages not in the wheel: {sorted(sources - tops)}"
