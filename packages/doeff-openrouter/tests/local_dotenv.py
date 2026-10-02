"""Load a local .env into the test process's environment — the one doeff-openrouter test module that touches it.

The live tests reach OpenRouter with a key the runner keeps in ``packages/doeff-openrouter/.env``;
pytest runs outside any doeff Program, so the key is handed over as an environment variable of the
test process. Variables already set win over the file. ``packages/doeff-openrouter/architecture.hy``
names this module and takes it out of DOEFF004 (agora-redesign #2861); other test modules still may
not read or write the environment.
"""

import os
from pathlib import Path


def load_dotenv(env_path: Path) -> None:
    """Set each ``KEY=VALUE`` of ``env_path`` that the environment does not already have."""
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
