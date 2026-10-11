"""置き場の entry の material を、この process で 1 つずつ照らし、合わない module を出す。"""

import pickle
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
import json  # noqa: E402,F401  (run_steps.py と同じ物を読み込む)

from doeff_effect_analyzer import result_cache as rc  # noqa: E402
from doeff_effect_analyzer.handler_effects import check_coverage  # noqa: E402,F401
from doeff_effect_analyzer.program_effects import analyze_program, reading_sources  # noqa: E402,F401

for path in Path(sys.argv[1]).rglob("*.pickle"):
    entry = pickle.load(path.open("rb"))
    print("entry", path.name[:12], "modules", len(entry.material))
    for used in entry.material:
        status = rc._stat_here(used.module)
        if status is None:
            print("  not found here:", used.module)
        elif rc._with_same_content(used, status) is None:
            print("  content differs:", used.module, status.path)
