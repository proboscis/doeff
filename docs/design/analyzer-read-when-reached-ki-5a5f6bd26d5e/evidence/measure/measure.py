"""閉じの検と同じ job と土台を analyzer に直に渡し、1 つの process の中で順に読んで秒を割る(測りだけ — repo の外)。"""

import contextlib
import hashlib
import importlib
import os
import re
import sys
import time
from pathlib import Path

import hy  # noqa: F401  — .hy の import を開く

import doeff_cluster

_loc = list(doeff_cluster.__path__)[0]
sys.path.insert(0, os.environ.get("MEASURE_ANALYZER") or str(Path(_loc).resolve().parents[2] / "doeff-effect-analyzer" / "python"))

from doeff_effect_analyzer import program_effects  # noqa: E402
from doeff_effect_analyzer.handler_effects import check_coverage  # noqa: E402
from doeff_effect_analyzer.program_effects import analyze_program, runs_where_performed  # noqa: E402

RECORDS = "controllers.foundation.records_job:records_job_foundation"
CASES = {
    "screen": ("controllers.screen.entry.system:agora_screen", "controllers.screen.entry.job_foundation:screen_job_foundation"),
    "deliver": ("controllers.messaging.entry.delivery_system:deliver", RECORDS),
    "route": ("controllers.messaging.entry.delivery_system:route", RECORDS),
    "attend": ("controllers.scheduling.entry.system:turn_attend", RECORDS),
    "keeper": ("controllers.scheduling.entry.system:turn_worker_keeper", RECORDS),
    "exhaustion": ("controllers.scheduling.entry.system:turn_profile_exhaustion", RECORDS),
    "profile-keeper": ("controllers.scheduling.entry.system:turn_profile_keeper", RECORDS),
    "dispatcher-records": ("controllers.scheduling.entry.system:turn_dispatcher", RECORDS),
    "dispatcher-host": ("controllers.scheduling.entry.system:turn_dispatcher", "controllers.foundation.host_job:host_job_foundation"),
    "cli-host-loop": ("controllers.scheduling.tests.test_turns_closure_unreadable:host_job_with_production_parts", "controllers.foundation.cli_host_job:cli_host_job_foundation"),
    "cli-host-turn": ("controllers.scheduling.tests.test_turns_closure_unreadable:host_turn_with_production_parts", "controllers.foundation.cli_host_job:cli_host_job_foundation"),
    "custody": ("controllers.custody_body.tests.closure_bare:custody_job_with_production_store", "controllers.custody_body.entry.system:custody_job_foundation"),
    "intake": ("controllers.messaging.entry.intake_api:intake_api", "controllers.foundation.records_listener_job:announcing_listener_job_foundation"),
    "toolset": ("controllers.agent_env.entry.system:agent_toolset_sync", "controllers.agent_env.entry.system:agent_toolset_job_foundation"),
    "durable-keeper": ("controllers.durable.entry.system:durable_keeper", "controllers.foundation.records_job:principal_records_job_foundation"),
    "durable-attempt": ("controllers.durable.entry.system:durable_attempt", "controllers.foundation.records_job:principal_records_job_foundation"),
    "daily-verify": ("controllers.daily_verify.entry.service:daily_verify", "controllers.foundation.workspace_job:workspace_job_foundation"),
    "daily-verify-task": ("controllers.daily_verify.entry.service:daily_verify_task_job", "controllers.foundation.workspace_job:workspace_job_foundation"),
    "custody-contract": ("controllers.daily_verify.entry.custody_contract_handlers:custody_contract_process", "controllers.daily_verify.entry.custody_contract_handlers:custody_contract_foundation"),
}
# 検の file ごとの組(日次では file ごとにどれかの session で走る)。
FILES = {
    "screen": ["screen"],
    "unreadable": ["exhaustion", "profile-keeper", "cli-host-loop", "cli-host-turn"],
    "delivery": ["deliver", "route"],
    "custody": ["custody"],
    "turns": ["attend", "keeper", "dispatcher-records", "dispatcher-host"],
    "daily-verify": ["daily-verify", "daily-verify-task", "custody-contract"],
    "keeper-sets": ["durable-keeper", "durable-attempt"],
    "intake": ["intake"],
    "toolset": ["toolset"],
}

expansion_s = [0.0]
split_s = {"expand": 0.0, "write": 0.0, "expand_n": 0}


@contextlib.contextmanager
def _observe():
    """展開の外れ(Hy の展開と置き場への書き)の秒を数えるため、analyzer の観測の口に付ける。"""
    t = time.perf_counter()
    try:
        yield
    finally:
        expansion_s[0] += time.perf_counter() - t


def _timed(name, function):
    """外れの内訳(Hy の展開と、派生の値の作りと書き)を数えるため、analyzer の関数 1 つを包んで秒を足す。"""

    def wrapper(*args, **kwargs):
        t = time.perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            split_s[name] += time.perf_counter() - t
            split_s["expand_n"] += name == "expand"

    return wrapper


if os.environ.get("MEASURE_READER_DIGEST"):
    # 試しの間だけ: 展開の木の鍵の reader の digest を、指した file の中身の digest に固定する(派生の値の形を変えない写しで、温まった木を共有するため)。
    _pinned = f"reader={hashlib.sha256(Path(os.environ['MEASURE_READER_DIGEST']).read_bytes()).hexdigest()}"
    program_effects._reader_digest = lambda: _pinned

program_effects.observe_expansions(_observe)
for _name, _attr in (("expand", "_expand_hy"), ("write", "_write_cached_tree")):
    # main の 520dadcb0 から展開は hy_expansion へ移った — 在る版でだけ包む(外れの内訳を数えるため)。
    if hasattr(program_effects, _attr):
        setattr(program_effects, _attr, _timed(_name, getattr(program_effects, _attr)))


def _resolve(spec):
    """検が import する job と土台の値を、module:名 の綴りから引くため。"""
    module, name = spec.split(":")
    return getattr(importlib.import_module(module), name)


def _canon_text(value):
    """番地を除いた綴り(関数や型の repr に番地が混ざるため)。"""
    text = value if isinstance(value, str) else repr(value)
    return re.sub(r"_hy_gensym_([A-Za-z_]*?)_?[0-9]+", r"_hy_gensym_\1_N", re.sub(r"0x[0-9a-f]+", "0x", text))


def _deep_digest(report):
    """ProgramEffects の木の全体(運ばれた Program・handled の scope・handler の節の emits と performs まで)の digest。
    同じ値を何度も通らないよう id で覚える(同じ process の中だけ)。"""
    memo = {}

    def name_of(obj):
        return f"{getattr(obj, '__module__', '?')}.{getattr(obj, '__qualname__', _canon_text(obj))}"

    def loc(location):
        return f"{location.file}:{location.line}"

    def unresolved(item):
        return (item.reason, _canon_text(item.text), loc(item.location), tuple(item.via))

    def handler(h):
        key = ("h", id(h))
        if key in memo:
            return memo[key]
        text = repr((
            h.name, h.basis.value,
            tuple((name_of(c.handles), program(c.emits), loc(c.location)) for c in h.clauses),
            tuple(unresolved(u) for u in h.unresolved),
            program(h.performs) if h.performs is not None else None,
        ))
        memo[key] = hashlib.sha256(_canon_text(text).encode()).hexdigest()
        return memo[key]

    def program(p):
        key = ("p", id(p))
        if key in memo:
            return memo[key]
        text = repr((
            _canon_text(p.target),
            tuple((name_of(u.effect), loc(u.location), tuple(u.via), u.forwarded) for u in p.effects),
            tuple((name_of(c.carrier), program(c.program), loc(c.location), tuple(c.via)) for c in p.carried),
            tuple(unresolved(u) for u in p.unresolved),
            tuple((tuple(handler(h) for h in s.handlers), program(s.program), loc(s.location), tuple(s.via)) for s in p.handled),
        ))
        memo[key] = hashlib.sha256(_canon_text(text).encode()).hexdigest()
        return memo[key]

    return program(report)[:12]


def run(name):
    """組 1 つを foundation-closure と同じ形(土台を引数 foundation に束ねる・運ばれた Program を数える)で読み、秒と答えの digest を出すため。"""
    job_spec, foundation_spec = CASES[name]
    t0 = time.perf_counter()
    job, foundation = _resolve(job_spec), _resolve(foundation_spec)
    t1 = time.perf_counter()
    before = expansion_s[0]
    report = analyze_program(job, bindings={"foundation": foundation})
    t2 = time.perf_counter()
    coverage = check_coverage(report, [], include=runs_where_performed)
    t3 = time.perf_counter()
    answer = (
        sorted(f"{g.effect.__module__}.{g.effect.__qualname__} ← {g.origin}" for g in coverage.gaps),
        sorted(coverage.unknown_handlers),
        sorted(f"{u.reason}: {u.text}({u.location})" for u in coverage.unresolved),
    )
    digest = hashlib.sha256(_canon_text(repr(answer)).encode()).hexdigest()[:12]
    deep = _deep_digest(report) if os.environ.get("MEASURE_DEEP") else "-"
    if os.environ.get("MEASURE_DUMP"):
        Path(os.environ["MEASURE_DUMP"]).mkdir(parents=True, exist_ok=True)
        Path(os.environ["MEASURE_DUMP"]).joinpath(f"{name}.txt").write_text(_canon_text("\n".join(map(str, answer))))
    print(f"  {name}: 木の digest {deep}", flush=True)
    print(
        f"{name}: import {t1 - t0:.1f}s  analyze {t2 - t1:.1f}s (うち展開の外れ {expansion_s[0] - before:.1f}s)  "
        f"coverage {t3 - t2:.1f}s  答え {digest}  外れの内訳 {split_s}",
        flush=True,
    )


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        for case in FILES[arg[5:]] if arg.startswith("file:") else [arg]:
            run(case)
