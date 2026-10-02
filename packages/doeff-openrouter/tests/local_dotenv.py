"""Read a local .env — the live tests' OpenRouter key the runner keeps in ``packages/doeff-openrouter/.env``.

pytest runs outside any doeff Program. The live test reads the key from its own fixture: the
environment variable first (through the ReadEnvironment effect and its real handler), then this
file. Nothing is written into the test process's environment any more (DOEFF004 — agora-redesign
#3012; this module used to copy the file into ``os.environ`` and was exempted from the rule, #2861).
"""

from pathlib import Path


def dotenv_values(env_path: Path) -> dict[str, str]:
    """The ``KEY=VALUE`` pairs of ``env_path`` (empty when the file is missing or unreadable)."""
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    try:
        for raw_line in env_path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key:
                values[key] = value.strip().strip("'\"")
    except OSError:
        # If we cannot read the file the live test falls back to the environment alone.
        return {}
    return values
