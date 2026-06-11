"""Pytest conftest hook — exposes the shared ``run_program`` helper to e2e tests."""
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
