# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Fails if the video says something its sources do not: a code line that is not in examples/effects_tour/spire.py, or
a number that differs from the output of measure/hundred.py (measure/hundred.log; see the usage at the top of that file).

Usage: uv run check_code.py
"""
import ast
import re
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
spire = (here / "../../../../../examples/effects_tour/spire.py").resolve().read_text().splitlines()
tsx = (here / "ArticleHundredFights.tsx").read_text()
log_path = here / "measure/hundred.log"
if not log_path.exists():
    sys.exit(f"no {log_path}: run measure/hundred.py first")
log = dict(line.split(": ", 1) for line in log_path.read_text().splitlines() if ": " in line)

bad = []
for block in re.findall(r"const \w+_CODE = \[(.*?)\n\];", tsx, re.S):
    for literal in re.findall(r"^  ('.*'),$", block, re.M):
        line = ast.literal_eval(literal)
        if not any(source.rstrip() == line.rstrip() for source in spire):
            bad.append(f"code line not in spire.py: {line}")
for name, literal in re.findall(r"const (\w+_EXPR) = ('.*');", tsx):
    if not any(ast.literal_eval(literal) in source for source in spire):
        bad.append(f"{name} not in spire.py")


def expect(what: str, shown: object, measured: object) -> None:
    if shown != measured:
        bad.append(f"{what}: the video says {shown}, the run says {measured}")


expect("RANDOM_WINS", re.search(r"RANDOM_WINS = '(.*)'", tsx)[1], log["random_wins_by_seed"])
for name, number in re.findall(r"(\w+): ([\d.]+)", re.search(r"const MEASURED = \{(.*?)\}", tsx)[1]):
    expect(name, float(number), float(log[name]))
expect("RESULTS_FIRST_THREE", f"[{re.search(r'RESULTS_FIRST_THREE = "(.*)"', tsx)[1]}]", log["results_first_three"])
for const, key in (("REACHED", "reached_scheduler"), ("DAMAGE", "damage_by_card")):
    pairs = re.findall(r"\['(\w+)', (\d+)\]", re.search(rf"const {const}\b.*?= (\[.*\]);", tsx)[1])
    expect(const, {name: int(number) for name, number in pairs}, ast.literal_eval(log[key]))

print("\n".join(bad) if bad else "code lines all in spire.py, numbers all from the run")
sys.exit(1 if bad else 0)
