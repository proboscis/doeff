"""jev_handler / jev_memo_handler / journal_handler: HTTP is answered by a scripted handler (no communication)."""

import json
import pickle

from doeff_core_effects import HttpRequest, HttpResponse, in_memory_cache_handler, scheduled
from doeff_jev import JevTarget, jev_handler, judge_stack
from doeff_system_one import JudgeError, Question, Verdict
from doeff_system_one._programs import judge

from doeff import Err, Ok, Pass, Resume, ResumeThrow, Try, do, run, with_handlers
from doeff import handler as _program_handler

DIRECT = JevTarget("https://api.typesafe.ai/v1/systemone", "jev-latest", "direct", "k-direct", "default")
SELF_HOSTED = JevTarget("http://judge-host:8646/v1/systemone", "local-judge-27b", "direct", None, "env")
ANSWER = {"answers": {"n": {"type": "noul", "noul": 0.8}, "c": {"type": "choice", "choice": "a", "confidence": 0.9}},
          "usage": {"input_tokens": 100}, "model": "jev-1.13.0"}


class HttpScript:
    """Maker of a handler that records HttpRequests and answers from a script (status, text | exception)."""

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
            status, text, *rest = step
            headers = rest[0] if rest else {}
            return (yield Resume(k, HttpResponse(status, headers, text.encode(), text, effect.url, 0.01)))
        return _program_handler(raw)


def _judged(program, layers):
    """Same shape as the caller's assembly: stack the handlers and run with doeff's run under the scheduler."""
    return run(scheduled(with_handlers(layers, program)))


def _run(program, target, script, journal=None, caller=""):
    layers = judge_stack(target, http=script.handler(), cache=in_memory_cache_handler(), journal=journal,
                         caller=caller)
    return _judged(program, layers)


def test_judge_becomes_one_post_and_a_verdict() -> None:
    script = HttpScript()

    @do
    def flow():
        verdict = yield judge({"x": 1}, {"n": Question(kind="noul", instructions="is"), "c": Question(kind="choice", instructions="w", options=(("a", "a"), ("b", "b")))})
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
        first = yield judge("same", {"n": Question(kind="noul", instructions="is")})
        second = yield judge("same", {"n": Question(kind="noul", instructions="is")})
        third = yield judge("same", {"n": Question(kind="noul", instructions="is")}, cacheable=False)
        return (first.answer("n").value, second.answer("n").value, third.answer("n").value)

    assert _run(flow(), DIRECT, script) == (0.8, 0.8, 0.8)
    assert len(script.requests) == 2  # the 2nd is from the cache, the 3rd is re-sent with cacheable=False


def test_http_error_and_transport_failure_become_judge_errors() -> None:
    script = HttpScript((400, '{"error":"invalid discriminator"}'), ConnectionError("refused"))

    @do
    def flow():
        a = yield Try(judge("s", {"n": Question(kind="noul", instructions="is")}, cacheable=False))
        b = yield Try(judge("s", {"n": Question(kind="noul", instructions="is")}, cacheable=False))
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
        return (yield Try(judge("s", {"n": Question(kind="noul", instructions="is")})))

    outcome = _run(flow(), no_key, script)
    assert isinstance(outcome, Err)
    assert outcome.error.kind == "no_credentials"
    assert script.requests == []


def test_foreign_target_sends_without_key() -> None:
    script = HttpScript()

    @do
    def flow():
        return (yield Try(judge("s", {"n": Question(kind="noul", instructions="is")})))

    outcome = _run(flow(), SELF_HOSTED, script)
    assert isinstance(outcome, Ok)
    assert "authorization" not in script.requests[0].headers


def test_journal_records_each_judgment(tmp_path) -> None:
    script = HttpScript()
    path = tmp_path / "journal.log"

    @do
    def flow():
        yield judge("a", {"n": Question(kind="noul", instructions="is")})
        yield judge("a", {"n": Question(kind="noul", instructions="is")})   # cache hit

    _run(flow(), DIRECT, script, journal=str(path))
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [line["ok"] for line in lines] == [True, True]
    assert lines[0]["tokens"] == 100
    assert lines[0]["host"] == "api.typesafe.ai"
    assert lines[1].get("cached") is True


def test_handler_passes_unrelated_effects() -> None:
    from doeff_core_effects import ReadWriterLog, Tell, state, writer

    @do
    def flow():
        yield Tell("hello")
        log = yield ReadWriterLog()
        return list(log)

    program = with_handlers([state(), writer, jev_handler(DIRECT)], flow())
    assert run(program) == ["hello"]


def test_caller_header_proxy_outcome_and_cache_mark_reach_the_verdict_and_the_journal(tmp_path) -> None:
    script = HttpScript((200, json.dumps(ANSWER), {"x-jev-proxy": "miss"}))
    path = tmp_path / "journal.log"

    @do
    def flow():
        asked = yield judge("a", {"n": Question(kind="noul", instructions="is"), "c": Question(kind="choice", instructions="w", options=(("a", "a"), ("b", "b")))})
        remembered = yield judge("a", {"n": Question(kind="noul", instructions="is"), "c": Question(kind="choice", instructions="w", options=(("a", "a"), ("b", "b")))})   # local cache
        return asked, remembered

    asked, remembered = _run(flow(), DIRECT, script, journal=str(path), caller="rulebook-advisor/Stop")
    assert script.requests[0].headers["x-jev-caller"] == "rulebook-advisor/Stop"
    assert len(script.requests) == 1
    assert (asked.proxy, asked.cached) == ("miss", False)
    assert (remembered.proxy, remembered.cached) == (None, True)
    assert remembered.input_tokens == asked.input_tokens  # keeps the values from when it was asked; the counting side excludes cached
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [(line["caller"], line["keys"], line.get("proxy"), line["cached"]) for line in lines] == [
        ("rulebook-advisor/Stop", ["n", "c"], "miss", False),
        ("rulebook-advisor/Stop", ["n", "c"], None, True),
    ]


def test_failure_rows_carry_the_caller_and_the_question_keys(tmp_path) -> None:
    script = HttpScript((502, "bad gateway", {"x-jev-proxy": "upstream-error"}))
    path = tmp_path / "journal.log"

    @do
    def flow():
        return (yield Try(judge("s", {"n": Question(kind="noul", instructions="is")}, cacheable=False)))

    outcome = _run(flow(), DIRECT, script, journal=str(path), caller="agora-messaging/land-classification")
    assert isinstance(outcome, Err)
    (line,) = [json.loads(line) for line in path.read_text().splitlines()]
    assert (line["ok"], line["caller"], line["keys"], line["status"], line["proxy"]) == (
        False, "agora-messaging/land-classification", ["n"], 502, "upstream-error")


def test_without_a_journal_the_row_reaches_the_outer_slog_sink_with_caller_and_keys(capsys) -> None:
    script = HttpScript((200, json.dumps(ANSWER), {"x-jev-proxy": "hit"}))

    @do
    def flow():
        return (yield judge("s", {"n": Question(kind="noul", instructions="is"), "c": Question(kind="choice", instructions="w", options=(("a", "a"), ("b", "b")))}, cacheable=False))

    layers = judge_stack(DIRECT, http=script.handler(), cache=in_memory_cache_handler(), journal=None,
                         caller="land-notice/notice_lead", log_stderr=True)
    verdict = _judged(flow(), layers)
    assert verdict.proxy == "hit"
    (line,) = [line for line in capsys.readouterr().err.splitlines() if "jev_judge" in line]
    assert "caller=land-notice/notice_lead" in line
    assert "keys=['n', 'c']" in line
    assert "proxy=hit" in line


def test_answers_cached_before_the_marks_existed_still_read() -> None:
    # An answer from a previous version (a pickled Verdict without the proxy / cached fields) can be read with the field defaults.
    old = Verdict.__new__(Verdict)
    old.__dict__.update({"answers": {}, "model": "jev-1.13.0", "input_tokens": 3, "elapsed_ms": 4})
    restored = pickle.loads(pickle.dumps(old))
    assert (restored.model, restored.proxy, restored.cached) == ("jev-1.13.0", None, False)


def test_without_a_target_the_stack_resolves_it_from_the_process_environment(monkeypatch, tmp_path) -> None:
    """A judge_stack that is not passed a destination resolves it from this process's environment (the same resolution as omitting target in the former run_judgment)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("JEV_WIRE", "JEV_API_KEY", "JEV_API_KEY_FILE", "TYPESAFE_API_KEY", "AI_GATEWAY_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JEV_BASE_URL", SELF_HOSTED.base_url)
    monkeypatch.setenv("JEV_MODEL", SELF_HOSTED.model)
    script = HttpScript()

    @do
    def flow():
        return (yield judge("s", {"n": Question(kind="noul", instructions="is")}, cacheable=False))

    verdict = _judged(flow(), judge_stack(http=script.handler(), journal=None))
    assert verdict.answers["n"].value == 0.8
    (request,) = script.requests
    assert request.url == SELF_HOSTED.base_url
    assert request.body["model"] == SELF_HOSTED.model
    assert "authorization" not in {name.lower() for name in request.headers}
