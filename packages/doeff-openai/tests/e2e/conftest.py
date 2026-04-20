"""Pytest conftest hook — exposes the shared ``run_program`` helper to e2e tests."""
from pathlib import Path
import sys

TESTS_DIR = Path(__file__).resolve().parent.parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
