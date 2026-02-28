
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TIME_PACKAGE_ROOT = ROOT / "packages" / "doeff-time" / "src"
EVENTS_PACKAGE_ROOT = ROOT / "packages" / "doeff-events" / "src"

for package_root in (TIME_PACKAGE_ROOT, EVENTS_PACKAGE_ROOT):
    if str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))
