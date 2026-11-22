# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Fails if a code line of the video is not a line (or the start of a line) of this video's measure/ program or of
examples/effects_tour/clocks.py, the example the scheduling part of the video shows.

Usage: uv run check_code.py   (indentation is not compared)
"""

import ast
import re
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
clocks = (here / "../../../../../examples/effects_tour/clocks.py").resolve()
sources = [line.strip() for path in [*sorted((here / "measure").glob("*.py")), clocks] for line in path.read_text().splitlines()]
shown = [
    ast.literal_eval(literal).strip()
    for tsx in sorted(here.glob("*.tsx"))
    for block in re.findall(r"const \w+_CODE = \[(.*?)\];", tsx.read_text(), re.S)
    for literal in re.findall(r"('(?:[^'\\]|\\.)*')", block)
]
bad = [line for line in shown if line and not any(source.startswith(line) for source in sources)]
for line in bad:
    print("not in measure/ or clocks.py:", line)
print(f"{len(shown)} code lines all in measure/ or clocks.py" if not bad else f"{len(bad)} lines differ")
sys.exit(1 if bad else 0)
