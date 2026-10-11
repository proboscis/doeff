"""照らしの正味の CPU(照らしの中で新しく import した秒を引く)と読み込みの CPU。mode は upper_bound.py と同じ。"""
import json
import sys
import time

mode = sys.argv[1]
preload = mode.endswith("+repr")
mode = mode.removesuffix("+repr")
dump = "/tmp/be59-digests.json"
start = time.process_time()
import hy  # noqa
import doeff_hy  # noqa
if preload:
    import hy.core.hy_repr  # noqa
from doeff_hy_bytecode_guard import macro_use
import os
if os.environ.get("BE59_NOSYNC"):
    from doeff_hy_bytecode_guard import loader_hooks
    macro_use._Walker._against_executed = lambda self, file: None
    loader_hooks._note_executed = lambda path: None

frames = []
import gc
gc_in = {"t": 0.0, "n": 0, "start": None}
gc_all = {"t": 0.0, "n": 0, "start": None}


def on_gc(phase, info):
    if phase == "start":
        gc_all["start"] = time.process_time()
    elif gc_all["start"] is not None:
        gc_all["t"] += time.process_time() - gc_all["start"]
        gc_all["n"] += 1
        gc_all["start"] = None
    if not frames:
        return
    if phase == "start":
        gc_in["start"] = time.process_time()
    elif gc_in["start"] is not None:
        gc_in["t"] += time.process_time() - gc_in["start"]
        gc_in["n"] += 1
        gc_in["start"] = None


gc.callbacks.append(on_gc)
net = {"check": 0.0, "imports": 0.0, "n": 0}
original_check = macro_use.record_is_current


def check(*args, **kwargs):
    frames.append([0.0])
    t = time.process_time()
    try:
        return original_check(*args, **kwargs)
    finally:
        inner = frames.pop()[0]
        elapsed = time.process_time() - t
        net["check"] += elapsed - inner
        net["n"] += 1
        if frames:
            frames[-1][0] += elapsed


original_import = macro_use._import_for_digest


def importing(name):
    loaded = name in sys.modules
    t = time.process_time()
    try:
        return original_import(name)
    finally:
        if not loaded and frames:
            elapsed = time.process_time() - t
            frames[-1][0] += elapsed
            net["imports"] += elapsed


if mode == "always-current":
    original_check = lambda record, hy_version, sha256_of: True  # noqa: E731
macro_use.record_is_current = check
macro_use._import_for_digest = importing
if mode in ("c", "ac", "ac-dump"):
    validated = set()
    stored = json.load(open(dump)) if mode == "ac" else {}
    written = {}
    original = macro_use._remembered

    def remembered(key, subject, walk, sha256_of):
        hit = macro_use._REMEMBERED.get(key)
        if hit is not None and hit.subject is subject and key in validated:
            return hit.closure
        if mode == "ac" and repr(key) in stored and hit is None:
            closure = macro_use.ClosureDigest(stored[repr(key)], ())
            macro_use._REMEMBERED[key] = macro_use.RememberedDigest(subject, (), closure)
            validated.add(key)
            return closure
        closure = original(key, subject, walk, sha256_of)
        validated.add(key)
        if closure.digest is not None:
            written[repr(key)] = closure.digest
        return closure

    macro_use._remembered = remembered
import importlib  # noqa

importlib.import_module("controllers.agora_sim.tests.test_emulated_screen")
cpu = time.process_time() - start
if mode == "ac-dump":
    json.dump(written, open(dump, "w"))
print(f"mode={mode} cpu={cpu:.3f} check_net={net['check']:.3f} n={net['n']} imports_inside={net['imports']:.3f} gc_in_check={gc_in['t']:.3f} gc_n={gc_in['n']} gc_all={gc_all['t']:.3f} gc_all_n={gc_all['n']}")
