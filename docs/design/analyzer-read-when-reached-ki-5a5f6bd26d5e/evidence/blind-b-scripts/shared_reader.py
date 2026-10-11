"""別々の解析で、同じ handler の答え(と節が握る reader)が同じ物かを出す。"""

import sys

sys.path.insert(0, sys.argv[1])
from doeff_effect_analyzer.handler_effects import analyze_handler  # noqa: E402

first = analyze_handler("blindb_mutual.handlers:handler_a")
second = analyze_handler("blindb_mutual.handlers:handler_a")
readers = [
    cell.cell_contents
    for answer in (first, second)
    for clause in answer.clauses[:1]
    for cell in clause.__dict__["_emits"].__closure__
    if type(cell.cell_contents).__name__ == "_Reader"
]
print("same HandlerEffects:", first is second, "| same _Reader:", readers[0] is readers[1])
