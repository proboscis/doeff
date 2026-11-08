# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Fails if a code line of the video is not a line (or the start of a line) of the article's examples
(examples/effects_tour/trading.py and test_trading.py) or of this video's measure/ program.

Usage: uv run check_code.py   (the "..." lines are elisions and are skipped; indentation is not compared)
"""

import ast
import re
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
examples = (here / "../../../../../examples/effects_tour").resolve()
sources = [
    line.strip()
    for path in [examples / "trading.py", examples / "test_trading.py", *sorted((here / "measure").glob("*.py"))]
    for line in path.read_text().splitlines()
]
shown = [
    ast.literal_eval(literal).strip()
    for tsx in sorted(here.glob("*.tsx"))
    for block in re.findall(r"const \w+_CODE = \[(.*?)\];", tsx.read_text(), re.S)
    for literal in re.findall(r"('(?:[^'\\]|\\.)*')", block)
]
bad = [
    line
    for line in shown
    if line not in ("", "...") and not any(source.startswith(line) for source in sources)
]
for line in bad:
    print("not in the examples or measure/:", line)
print(
    f"{len(shown)} code lines all in the examples or measure/"
    if not bad
    else f"{len(bad)} lines differ"
)
sys.exit(1 if bad else 0)
