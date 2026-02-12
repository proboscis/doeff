from __future__ import annotations

import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[3]

sys.path.insert(0, str(_ROOT / "packages" / "doeff-sim" / "src"))
sys.path.insert(0, str(_ROOT / "packages" / "doeff-time" / "src"))
sys.path.insert(0, str(_ROOT / "packages" / "doeff-events" / "src"))
