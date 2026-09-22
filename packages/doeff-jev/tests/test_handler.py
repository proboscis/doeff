"""jev_handler / jev_memo_handler / journal_handler — HTTP は台本の handler で答える(通信なし)。"""

import json

from doeff_core_effects import HttpRequest, HttpResponse
from doeff_core_effects.cache_handlers import in_memory_cache_handler
from doeff_jev import JevTarget, jev_handler, judge_stack
from doeff_jev.wiring import run_judgment
from doeff_system_one import JudgeError, choice, judge, noul
from doeff_vm import Err, Ok

from doeff import Pass, Resume, ResumeThrow, Try, do, run, with_handlers
from doeff import handler as _program_handler

DIRECT = JevTarget("https://api.typesafe.ai/v1/systemone", "jev-latest", "direct", "k-direct", "default")
SEIMF = JevTarget("http://zeus:8646/v1/systemone", "seimf-27b", "direct", None, "env")
ANSWER = {"answers": {"n": {"type": "noul", "noul": 0.8}, "c": {"type": "choice", "choice": "a", "confidence": 0.9}},
          "usage": {"input_tokens": 100}, "model": "jev-1.13.0"}


class HttpScript:
    """HttpRequest を記録し、台本(status, text | 例外)で答える handler の作り手。"""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.requests: list[HttpRequest] = []

    def handler(self):
        script = self

        @do
        def raw(effect, k):
            if not isinstance(effect, HttpRequest):
                return (yield Pass(effect, k))
            script.requests.append(effect)
            step = script.steps.pop(0) if script.steps else (200, json.dumps(ANSWER))
            if isinstance(step, BaseException):
                return (yield ResumeThrow(k, step))
            status, text = step
            return (yield Resume(k, HttpResponse(status, {}, text.encode(), text, effect.url, 0.01)))
        return _program_handler(raw)


def _run(program, target, script, journal=None):
    layers = judge_stack(target, http=script.handler(), cache=in_memory_cache_handler(), journal=journal)
    return run_judgment(program, target=target, layers=layers)


def test_judge_becomes_one_post_and_a_verdict() -> None:
    script = HttpScript()

    @do
    def flow():
        verdict = yield judge({"x": 1}, {"n": noul("is"), "c": choice("w", ["a", "b"])})
        return verdict

    verdict = _run(flow(), DIRECT, script)
    assert verdict.model == "jev-1.13.0"
    assert verdict.input_tokens == 100
    assert verdict.answer("n").value == 0.8
    assert verdict.answer("c").value == "a"
    request = script.requests[0]
    assert request.method == "POST"
    assert request.url == DIRECT.base_url
    assert request.headers["authorization"] == "Bearer k-direct"
    assert request.body["model"] == "jev-latest"
    assert request.body["questions"]["n"] == {"type": "noul", "instructions": "is"}


def test_cache_hit_skips_the_second_post_but_uncacheable_does_not() -> None:
    script = HttpScript()

    @do
    def flow():
        first = yield judge("same", {"n": noul("is")})
        second = yield judge("same", {"n": noul("is")})
        third = yield judge("same", {"n": noul("is")}, cacheable=False)
        return (first.answer("n").value, second.answer("n").value, third.answer("n").value)

    assert _run(flow(), DIRECT, script) == (0.8, 0.8, 0.8)
    assert len(script.requests) == 2  # 2 回目は cache、3 回目は cacheable=False で再送


def test_http_error_and_transport_failure_become_judge_errors() -> None:
    script = HttpScript((400, '{"error":"invalid discriminator"}'), ConnectionError("refused"))

    @do
    def flow():
        a = yield Try(judge("s", {"n": noul("is")}, cacheable=False))
        b = yield Try(judge("s", {"n": noul("is")}, cacheable=False))
        return a, b

    a, b = _run(flow(), DIRECT, script)
    assert isinstance(a, Err)
    assert isinstance(a.error, JudgeError)
    assert a.error.kind == "http"
    assert a.error.status == 400
    assert isinstance(b, Err)
    assert b.error.kind == "transport"
    assert "refused" in b.error.detail


def test_missing_key_for_typesafe_fails_without_a_request() -> None:
    script = HttpScript()
    no_key = JevTarget(DIRECT.base_url, DIRECT.model, "direct", None, "default")

    @do
    def flow():
        return (yield Try(judge("s", {"n": noul("is")})))

    outcome = _run(flow(), no_key, script)
    assert isinstance(outcome, Err)
    assert outcome.error.kind == "no_credentials"
    assert script.requests == []


def test_foreign_target_sends_without_key() -> None:
    script = HttpScript()

    @do
    def flow():
        return (yield Try(judge("s", {"n": noul("is")})))

    outcome = _run(flow(), SEIMF, script)
    assert isinstance(outcome, Ok)
    assert "authorization" not in script.requests[0].headers


def test_journal_records_each_judgment(tmp_path) -> None:
    script = HttpScript()
    path = tmp_path / "journal.log"

    @do
    def flow():
        yield judge("a", {"n": noul("is")})
        yield judge("a", {"n": noul("is")})   # cache hit

    _run(flow(), DIRECT, script, journal=str(path))
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [line["ok"] for line in lines] == [True, True]
    assert lines[0]["tokens"] == 100
    assert lines[0]["host"] == "api.typesafe.ai"
    assert lines[1].get("cached") is True


def test_handler_passes_unrelated_effects() -> None:
    from doeff_core_effects import Tell, state, writer, writer_log

    @do
    def flow():
        yield Tell("hello")
        log = yield writer_log()
        return list(log)

    program = with_handlers([state(), writer, jev_handler(DIRECT)], flow())
    assert run(program) == ["hello"]
