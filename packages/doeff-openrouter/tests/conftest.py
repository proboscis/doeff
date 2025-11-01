"""Test configuration for doeff-openrouter."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _load_dotenv(env_path: Path) -> None:
    if not env_path.exists():
        return
    try:
        for raw_line in env_path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        # If we cannot read the file we fall back to existing environment.
        pass


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_load_dotenv(PACKAGE_ROOT / ".env")

SRC_DIR = PACKAGE_ROOT / "src"
if SRC_DIR.exists():
    sys.path.insert(0, str(SRC_DIR))
