"""答えの置き場の dir の下の各 entry の、material(鍵の module)のうち blindb_mutual の物と、答えを出す。"""

import pickle
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
for path in sorted(Path(sys.argv[1]).rglob("*.pickle")):
    if "results" not in path.parts:
        continue
    with path.open("rb") as handle:
        entry = pickle.load(handle)
    mods = [m.module for m in entry.material if m.module.startswith(("blindb_mutual", "doeff_effect_analyzer"))]
    print(path.parent.name[:12], path.name[:12], mods, entry.value)
