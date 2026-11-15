"""The runs behind the article4-live video: every value it shows, from the examples of the article "Record and
replay" (examples/record_and_replay/), run as the article runs them.

Usage (from the repository root): uv run python tools/docs-video/src/pages/article4-live/measure/live.py > tools/docs-video/src/pages/article4-live/measure/live.log
"""

import subprocess
import sys
from pathlib import Path

examples = Path(__file__).resolve().parents[6] / "examples" / "record_and_replay"

# l4-l5: live.py records from its local server (served: 2), then answers from the file with the server stopped.
# l7: the three files of the article, each run on its own.
for name in ("releases.py", "on_disk.py", "live.py"):
    done = subprocess.run(
        [sys.executable, name], cwd=examples, capture_output=True, text=True, check=True
    )
    print(f"python {name}:")
    for line in done.stdout.splitlines():
        print("   ", line)
