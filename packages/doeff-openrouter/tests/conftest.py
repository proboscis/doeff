"""Test configuration for doeff-openrouter."""


import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


SRC_DIR = PACKAGE_ROOT / "src"
if SRC_DIR.exists():
    sys.path.insert(0, str(SRC_DIR))
