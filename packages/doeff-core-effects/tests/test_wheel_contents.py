"""doeff-core-effects の組んだ wheel が、この配布の top-level の package を全部持つ事の検(agora-redesign #3866)。

editable の環境(開発の venv)は package の dir を sys.path に足すだけなので、配布の設定(pyproject.toml の
[tool.hatch.build.targets.wheel] packages)に名が無い package も import が通る。wheel にだけ入らず、本番(wheel で入れる worker)で
import が落ちる形を、ここで本物の uv build で組んで名指す — doeff_lifeline が抜けると worker の shim が起きず、全部の job が起きない。
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
    assert sources <= tops, f"wheel に入らない top-level の package: {sorted(sources - tops)}"
