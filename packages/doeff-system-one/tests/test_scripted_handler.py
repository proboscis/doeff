"""台本 handler で Judge を走らせる — program 側は provider を知らない。"""

from doeff_core_effects import await_handler, scheduled, slog_discard_handler, try_handler
from doeff_system_one import (
    Answer,
    JudgeError,
    Question,
    ScriptedJudgeState,
    judge_many,
    scripted_judge_handler,
)
from doeff_system_one._judgment import answered
from doeff_system_one._programs import judge

from doeff import Err, Ok, Try, do, run, with_handlers


def _run(program, *layers):
    return run(scheduled(with_handlers([await_handler, try_handler, slog_discard_handler, *layers], program)))


def test_program_reads_verdict_from_scripted_answers() -> None:
    state = ScriptedJudgeState()
    handler = scripted_judge_handler({"vague": Answer.noul(0.9), "kind": Answer(kind="choice", value="実装", confidence=0.8)}, state)

    @do
    def flow():
        verdict = yield judge({"prompt": "直して"}, {"vague": Question(kind="noul", instructions="曖昧か"), "kind": Question(kind="choice", instructions="種類", options=(("調査", "調査"), ("実装", "実装")))})
        return (answered(verdict.answer("vague"), 0.6), verdict.answer("kind").value, verdict.model)

    assert _run(flow(), handler) == (True, "実装", "scripted")
    assert state.count == 1
    assert state.calls[0].state == {"prompt": "直して"}


def test_scripted_error_reaches_the_program_as_judge_error() -> None:
    handler = scripted_judge_handler(lambda _effect: JudgeError("http", "500 boom", status=500))

    @do
    def flow():
        outcome = yield Try(judge("s", {"q": Question(kind="noul", instructions="x")}))
        return outcome

    outcome = _run(flow(), handler)
    assert isinstance(outcome, Err)
    assert isinstance(outcome.error, JudgeError)
    assert outcome.error.kind == "http"
    assert outcome.error.status == 500


def test_missing_scripted_answer_is_malformed() -> None:
    handler = scripted_judge_handler({"other": Answer.noul(0.5)})

    @do
    def flow():
        return (yield Try(judge("s", {"q": Question(kind="noul", instructions="x")})))

    outcome = _run(flow(), handler)
    assert isinstance(outcome, Err)
    assert outcome.error.kind == "malformed"


def test_judge_many_keeps_input_order_and_runs_each_state() -> None:
    state = ScriptedJudgeState()
    handler = scripted_judge_handler(lambda effect: __import__("doeff_system_one").Verdict(
        answers={"p": Answer.noul(float(effect.state))}, model="scripted"), state)

    @do
    def flow():
        verdicts = yield judge_many(["0.1", "0.2", "0.3"], {"p": Question(kind="noul", instructions="x")})
        return [v.answer("p").value for v in verdicts]

    assert _run(flow(), handler) == [0.1, 0.2, 0.3]
    assert state.count == 3


def test_ok_outcome_wraps_verdict() -> None:
    handler = scripted_judge_handler({"q": Answer.noul(0.2)})

    @do
    def flow():
        return (yield Try(judge("s", {"q": Question(kind="noul", instructions="x")})))

    outcome = _run(flow(), handler)
    assert isinstance(outcome, Ok)
    assert outcome.value.answer("q").value == 0.2
