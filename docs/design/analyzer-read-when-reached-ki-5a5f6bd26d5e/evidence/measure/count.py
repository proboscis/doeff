"""analyzer の読みの重なりを数える(測りだけ — repo の外)。measure.py の前に読み込み、組ごとに数を出す。

数える物: reader.facts の外れ(実際に body を読んだ回数)と、その別の (関数, 束縛) の数・_report_facts と absorb の回数・
analyze_handler の回数と別の handler 関数の数・節の emits を読んだ回数。
"""

import os
import runpy
import sys
import time
from collections import Counter

sys.argv = [sys.argv[0], *sys.argv[1:]]
measure = os.path.join(os.path.dirname(__file__), "measure.py")

# measure.py が analyzer の path を足してから import するので、先に同じ path を足して包む。
import doeff_cluster  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, os.environ.get("MEASURE_ANALYZER") or str(Path(list(doeff_cluster.__path__)[0]).resolve().parents[2] / "doeff-effect-analyzer" / "python"))

from doeff_effect_analyzer import handler_effects, program_effects  # noqa: E402

counts = Counter()
distinct_facts = set()
distinct_handlers = Counter()
timing = Counter()

_orig_facts = program_effects._Reader.facts


def facts(self, function, bound=program_effects._NO_BINDINGS):
    key = program_effects._ReadKey(function, bound.key)
    if key not in self._facts:
        counts["facts の外れ"] += 1
        distinct_facts.add((id(function), bound.key))
    return _orig_facts(self, function, bound)


program_effects._Reader.facts = facts

_orig_report_facts = program_effects._report_facts


def report_facts(*args, **kwargs):
    counts["_report_facts"] += 1
    return _orig_report_facts(*args, **kwargs)


program_effects._report_facts = report_facts
handler_effects._report_facts = report_facts

_orig_analyze = handler_effects._analyze_handler


def analyze(obj, label, *, depth):
    counts["_analyze_handler"] += 1
    function = handler_effects._handler_function(obj)
    distinct_handlers[getattr(function, "__qualname__", repr(obj))] += 1
    return _orig_analyze(obj, label, depth=depth)


handler_effects._analyze_handler = analyze

_orig_report = program_effects._report


def report_twice(reader, root, bound, *, label, active=frozenset()):
    """辿り直しだけの秒を知るため、事実を読み終えた同じ reader で同じ報告をもう一度作って秒を数える。"""
    first = _orig_report(reader, root, bound, label=label, active=active)
    before = counts["_report_facts"]
    t = time.perf_counter()
    again = _orig_report(reader, root, bound, label=label, active=active)
    timing["辿り直しだけ"] += time.perf_counter() - t
    counts["辿り直しの報告"] += counts["_report_facts"] - before
    counts["_report_facts"] = before
    assert again == first
    return first


if os.environ.get("COUNT_REWALK"):
    program_effects._report = report_twice


def report(name):
    print(
        f"  数 {name}: facts の外れ {counts['facts の外れ']}・別の (関数, 束縛) {len(distinct_facts)}・"
        f"_report_facts {counts['_report_facts']}・handler の読み {counts['_analyze_handler']}・別の handler {len(distinct_handlers)}・"
        f"辿り直しだけ {timing['辿り直しだけ']:.2f}s({counts['辿り直しの報告']} 報告)",
        flush=True,
    )
    timing.clear()
    top = ", ".join(f"{k} {v}" for k, v in distinct_handlers.most_common(8))
    print(f"  多く読んだ handler: {top}", flush=True)
    counts.clear()
    distinct_facts.clear()
    distinct_handlers.clear()


namespace = runpy.run_path(measure, run_name="__measure__")
for arg in sys.argv[1:]:
    for case in namespace["FILES"][arg[5:]] if arg.startswith("file:") else [arg]:
        namespace["run"](case)
        report(case)
