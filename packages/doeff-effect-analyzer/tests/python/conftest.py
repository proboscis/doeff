"""Make the pure-Python front end importable without building the Rust extension.

The package is a maturin mixed project (``python/`` + the ``_native`` extension).
The Python front end (``program_effects`` / ``handler_effects``) does not need
the extension, so tests put ``python/`` on ``sys.path`` when the package is not
installed.
"""

import importlib.util
import sys
from pathlib import Path

if importlib.util.find_spec("doeff_effect_analyzer") is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))
