import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parents[2]
TIME_PACKAGE_ROOT = ROOT / "packages" / "doeff-time" / "src"
EVENTS_PACKAGE_ROOT = ROOT / "packages" / "doeff-events" / "src"

# TESTS_DIR makes the uniquely-named `time_test_support` helper module
# importable even when this suite is collected together with other
# testpaths (pytest only prepends a test file's own directory lazily).
for package_root in (TIME_PACKAGE_ROOT, EVENTS_PACKAGE_ROOT, TESTS_DIR):
    if str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))
