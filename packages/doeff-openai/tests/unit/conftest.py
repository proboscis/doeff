"""Pytest conftest hook — exposes the shared ``run_program`` helper to unit tests.

The actual implementation lives in ``tests/_runner.py`` (one directory up)
so the e2e suite can import it via a relative path too. We insert the
parent ``tests/`` dir on ``sys.path`` so ``import _runner`` works from any
test under this package.
"""
from pathlib import Path
import sys

TESTS_DIR = Path(__file__).resolve().parent.parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
