# Runs pyright (strict) and mypy (--strict) on probe.py and prints what each checker says, line by line, with the
# paths cut down to the file name. Run from the repository root: uv run python <this file>.
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBE = HERE / "probe.py"


def checker(name: str, command: list[str]) -> None:
    """Prints one checker's verdict on probe.py, so the video shows what the checker really said."""
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    print(f"$ {name} probe.py  (exit {done.returncode})")
    for line in (done.stdout + done.stderr).splitlines():
        line = re.sub(r"\S*probe\.py", "probe.py", line).strip()
        if not line or line.startswith("No configuration") or "new version" in line or "PYRIGHT_PYTHON" in line:
            continue
        print("  " + re.sub(r"\s+", " ", line))


checker("pyright", ["pyright", str(PROBE)])
checker("mypy --strict", ["mypy", "--strict", "--no-error-summary", "--hide-error-context", str(PROBE)])
