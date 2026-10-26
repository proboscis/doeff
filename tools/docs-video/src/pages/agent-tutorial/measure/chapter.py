# The chapter's own modules for the measure runs: writes every ```python file=<name>``` block of
# docs/19-agent-tutorial.md (review.py, scripted.py, review_all.py) into a temporary directory and puts it first on
# sys.path, so each run imports the code exactly as the page prints it.
import re
import sys
import tempfile
from pathlib import Path

PAGE = Path(__file__).resolve().parents[6] / "docs" / "19-agent-tutorial.md"
FILE_BLOCK = re.compile(r"^```python file=(\S+)\n(.*?)^```$", re.MULTILINE | re.DOTALL)


def load() -> Path:
    """Make the page's modules importable, so a run measures the page's code and not a copy that may drift."""
    where = Path(tempfile.mkdtemp(prefix="agent-tutorial-"))
    for name, body in FILE_BLOCK.findall(PAGE.read_text(encoding="utf-8")):
        (where / name).write_text(body, encoding="utf-8")
    sys.path.insert(0, str(where))
    return where
