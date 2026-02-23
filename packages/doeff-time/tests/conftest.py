from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACKAGE_PATHS = (
    ROOT / "packages" / "doeff-time" / "src",
    ROOT / "packages" / "doeff-events" / "src",
)

for package_path in PACKAGE_PATHS:
    package_path_str = str(package_path)
    if package_path_str not in sys.path:
        sys.path.insert(0, package_path_str)
