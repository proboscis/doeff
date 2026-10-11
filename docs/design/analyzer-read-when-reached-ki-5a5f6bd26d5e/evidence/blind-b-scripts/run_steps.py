"""手順を 1 process の中で順に撃つ: plain:<program>(reading_sources の中で閉じの解析)・
cached:<program>(result_cache.cached_result を通す)。引数 1 は package を置いた dir。"""

import json
import sys

import hy  # noqa: F401  (.hy の import hook — 下流の pytest は conftest で先に読み込む)

sys.path.insert(0, sys.argv[1])

from doeff_effect_analyzer import result_cache  # noqa: E402
from doeff_effect_analyzer.handler_effects import check_coverage  # noqa: E402
from doeff_effect_analyzer.program_effects import analyze_program, reading_sources  # noqa: E402

PKG = "blindb_mutual"


def coverage_of(name):
    cov = check_coverage(analyze_program(f"{PKG}.programs:{name}"), [])
    return {
        "gaps": [f"{g.effect.__name__} by {g.origin}" for g in cov.gaps],
        "unknown": list(cov.unknown_handlers),
        "complete": cov.complete,
    }


def step(spec):
    kind, name = spec.split(":")
    if kind == "plain":
        found = reading_sources(lambda: coverage_of(name))
        read = sorted(m for m in found.read if m.startswith(PKG))
        return {"step": spec, "answer": found.value, "read": read}
    value = result_cache.cached_result(("ki5a5f-blind-B-demo", name), lambda: coverage_of(name))
    return {"step": spec, "answer": value}


import doeff_effect_analyzer.handler_effects as _h  # noqa: E402

print("# analyzer:", _h.__file__)
for spec in sys.argv[2:]:
    print(json.dumps(step(spec), ensure_ascii=False))
