# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Fails if a code line of the video is not a line (or the start of a line) of examples/effects_tour/spire.py.

Usage: uv run check_code.py   (the "..." lines are elisions and are skipped)
"""
import ast
import re
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
spire = (here / "../../../../../examples/effects_tour/spire.py").resolve().read_text().splitlines()
tsx = (here / "XFirstPost.tsx").read_text()
bad = 0
for block in re.findall(r"const \w+_CODE = \[(.*?)\n\];", tsx, re.S):
    for literal in re.findall(r"^  ('.*'),$", block, re.M):
        line = ast.literal_eval(literal)
        if line.strip() == "...":
            continue
        if not any(source.rstrip().startswith(line.rstrip()) for source in spire):
            print("not in spire.py:", line)
            bad += 1
print("code lines all in spire.py" if not bad else f"{bad} lines differ")
sys.exit(1 if bad else 0)
