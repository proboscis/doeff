"""claude_judge_handler — Judge を 1 回きりの print(ClaudePrintOnce)へ運ぶ handler。print は fake の handler で答える(CLI を起こさない)。"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import hy  # noqa: F401  # .hy import hook — 1 回きりの print の effect と fake は Hy の module doeff_claude_code に住む
import pytest
from doeff_claude_code.effects import ClaudePrintOnce, PrintAnswered, PrintFailed, PrintTimedOut
from doeff_claude_code.fake import fake_claude_print_handler
from doeff_claude_code.lines import Usage
from doeff_claude_code.values import LentCredential
from doeff_claude_judge import (
    BorrowJudgeCredential,
    ClaudeJudgeTarget,
    ClaudeQuery,
    JudgeCredential,
    NoJudgeCredential,
    claude_judge_stack,
    fixed_credential_lender,
    prepare,
    run_judgment,
    scripted_printer,
)
from doeff_hy.frozen import freeze_json
from doeff_system_one import Judge, JudgeError, choice, noul, score
from doeff_vm import Err

from doeff import Pass, Resume, ResumeThrow, Try, do
from doeff import handler as _program_handler

if TYPE_CHECKING:
    from doeff_hy.frozen import JsonIn  # 型の宣言(frozen.pyi)だけに在る JSON の値の別名

QUESTIONS = {
    "plain": noul("Can a first-time reader decide from the document?"),
    "plain_where": choice("Which place most clearly fails plain?", {"p0": "The opening", "none": "None of these places"}),
}
JUDGE = Judge(state={"document": "the document"}, questions=QUESTIONS, timeout_seconds=90.0, cacheable=False)
ANSWER = {"plain": 0.8125, "plain_where": {"p0": 0.625, "none": 0.375}}
TARGET = ClaudeJudgeTarget(model="claude-test", config_dir="/h/.claude", env={"PATH": "/bin", "HOME": "/h"},
                           thinking_tokens=0, effort="low")
LENT = JudgeCredential(access_token="lent-token", expires_at=4102444800.0, subscription_type="max")


def _answered(structured: JsonIn) -> PrintAnswered:
    return PrintAnswered(structured_output=freeze_json(structured), usage=Usage(input_tokens=1000, cache_creation_input_tokens=200,
                                                                   cache_read_input_tokens=300, output_tokens=50),
                         cost_usd=0.0125, models=("claude-test-5",), elapsed_ms=4200, api_ms=3100)


def _printer(outcome: object, captured: dict[str, ClaudePrintOnce]):
    """1 回きりの print に outcome で答える fake(受けた宣言を captured["request"] に置く)。"""

    @do
    def respond(request: ClaudePrintOnce):
        captured["request"] = request
        return outcome

    return fake_claude_print_handler(respond)


def _no_print():
    """呼ばれたら赤にする fake(print の前に断る判定のため)。"""

    @do
    def respond(request: ClaudePrintOnce):
        raise AssertionError(f"print は出ないはず: {request.model}")

    return fake_claude_print_handler(respond)


def _lender(answer: object):
    """BorrowJudgeCredential に answer(資格・借りられない・例外)で答える handler。"""

    @do
    def raw(effect, k):
        if not isinstance(effect, BorrowJudgeCredential):
            return (yield Pass(effect, k))
        if isinstance(answer, BaseException):
            return (yield ResumeThrow(k, answer))
        return (yield Resume(k, answer))

    return _program_handler(raw)


def _run(judge: Judge, *, target: ClaudeJudgeTarget = TARGET, printer=None, lender=None):
    @do
    def flow():
        return (yield judge)

    return run_judgment(flow(), target=target, layers=claude_judge_stack(target, printer=printer, lender=lender))


def _refusal(judge: Judge, **kwargs) -> JudgeError:
    """判定が JudgeError で断られる事を確かめて、その誤りを取るため。"""

    @do
    def flow():
        return (yield Try(judge))

    target = kwargs.pop("target", TARGET)
    outcome = run_judgment(flow(), target=target, layers=claude_judge_stack(target, **kwargs))
    assert isinstance(outcome, Err), outcome
    assert isinstance(outcome.error, JudgeError), outcome.error
    return outcome.error


def test_a_judge_becomes_one_print_and_a_verdict() -> None:
    captured: dict[str, ClaudePrintOnce] = {}
    verdict = _run(JUDGE, printer=_printer(_answered(ANSWER), captured), lender=_lender(LENT))
    # 確率は CLI の答えの値のまま(丸めない)。
    assert verdict.answer("plain").value == 0.8125
    assert verdict.answer("plain_where").probabilities == {"p0": 0.625, "none": 0.375}
    # model = CLI が名乗った model・input_tokens = 読んだ token の全部(cache の作り・読みを含む)・elapsed_ms = CLI の process の秒。
    assert (verdict.model, verdict.input_tokens, verdict.elapsed_ms) == ("claude-test-5", 1500, 4200)
    request = captured["request"]
    query = prepare(JUDGE)
    assert (request.system_prompt, request.input, request.json_schema) == (query.system, query.user, query.schema_json)
    assert (request.model, request.thinking_tokens, request.effort) == ("claude-test", 0, "low")
    # 待ちの上限 = Judge の timeout_seconds(CLI の起動と終わりの秒を含む)。
    assert request.timeout_seconds == 90.0
    assert (request.home.config_dir, dict(request.home.env)) == ("/h/.claude", {"PATH": "/bin", "HOME": "/h"})


def test_the_borrowed_credential_is_handed_to_the_print_as_a_lent_credential() -> None:
    captured: dict[str, ClaudePrintOnce] = {}
    _run(JUDGE, printer=_printer(_answered(ANSWER), captured), lender=_lender(LENT))
    assert captured["request"].credential == LentCredential(access_token="lent-token", expires_at=4102444800.0,
                                                            subscription_type="max")


def test_a_target_that_does_not_borrow_prints_with_the_home_credential() -> None:
    captured: dict[str, ClaudePrintOnce] = {}
    target = ClaudeJudgeTarget(model="claude-test", config_dir="/h/.claude", env={"PATH": "/bin"}, borrow_credential=False)
    _run(JUDGE, target=target, printer=_printer(_answered(ANSWER), captured))
    assert captured["request"].credential is None


def test_a_refused_borrow_is_no_credentials_and_prints_nothing() -> None:
    error = _refusal(JUDGE, printer=_no_print(), lender=_lender(NoJudgeCredential("no account for the judge")))
    assert error.kind == "no_credentials"
    assert "no account for the judge" in error.detail
    raised = _refusal(JUDGE, printer=_no_print(), lender=_lender(RuntimeError("lender is down")))
    assert raised.kind == "no_credentials"
    assert "lender is down" in raised.detail


def test_an_api_error_of_the_cli_is_an_http_refusal_with_its_status() -> None:
    failed = PrintFailed(exit_code=1, api_status=401, detail="Failed to authenticate. API Error: 401", stderr_tail="")
    error = _refusal(JUDGE, printer=_printer(failed, {}), lender=_lender(LENT))
    assert (error.kind, error.status) == ("http", 401)


def test_a_cli_that_ends_without_an_answer_or_runs_past_the_wait_is_a_transport_refusal() -> None:
    crashed = PrintFailed(exit_code=2, api_status=None, detail="no answer line", stderr_tail="boom")
    assert _refusal(JUDGE, printer=_printer(crashed, {}), lender=_lender(LENT)).kind == "transport"
    timed_out = _refusal(JUDGE, printer=_printer(PrintTimedOut(seconds=90.0, stderr_tail=""), {}), lender=_lender(LENT))
    assert timed_out.kind == "transport"
    assert "90" in timed_out.detail


def test_an_answer_out_of_shape_is_a_malformed_refusal() -> None:
    error = _refusal(JUDGE, printer=_printer(_answered({**ANSWER, "plain": 1.5}), {}), lender=_lender(LENT))
    assert error.kind == "malformed"


def test_a_judge_the_cli_cannot_take_is_refused_before_borrowing_or_printing() -> None:
    scored = Judge(state="x", questions={"grade": score("How good?", ["low", "high"])})
    error = _refusal(scored, printer=_no_print(), lender=_lender(RuntimeError("no borrow before refusing")))
    assert error.kind == "unsupported"


def test_the_scripted_printer_answers_from_the_query_without_the_cli() -> None:
    # 使い手の検の fake(doeff_claude_code を import しない): 問いの部品と model → 構造化の答え。
    def answer(query: ClaudeQuery, model: str) -> JsonIn:
        assert query == prepare(JUDGE)
        assert model == "claude-test"
        return ANSWER

    verdict = _run(JUDGE, printer=scripted_printer(answer), lender=fixed_credential_lender(LENT))
    assert verdict.answer("plain").value == 0.8125
    assert verdict.model == "claude-test"


def test_a_judge_credential_hides_its_token() -> None:
    assert "lent-token" not in repr(LENT)
    with pytest.raises(ValueError, match="access_token"):
        JudgeCredential(access_token="", expires_at=None)
    with pytest.raises(ValueError, match="access_token"):
        JudgeCredential(access_token="two\nlines", expires_at=None)


def test_a_target_refuses_what_the_cli_cannot_take() -> None:
    for changes in ({"model": ""}, {"config_dir": ""}, {"thinking_tokens": -1}, {"thinking_tokens": True},
                    {"env": {"PATH": 1}}):
        with pytest.raises((TypeError, ValueError)):
            ClaudeJudgeTarget(**{"model": "claude-test", "config_dir": "/h", "env": {}, **changes})


def test_the_verdict_input_tokens_count_only_what_the_cli_named() -> None:
    # usage の欄を CLI が名乗らなければ 0 を足す(数を発明しない — 名乗った欄だけの和)。
    partial = PrintAnswered(structured_output=freeze_json(ANSWER), usage=Usage(input_tokens=7), cost_usd=None, models=(),
                            elapsed_ms=10, api_ms=None)
    verdict = _run(JUDGE, printer=_printer(partial, {}), lender=_lender(LENT))
    assert verdict.input_tokens == 7
    # CLI が model を名乗らなければ宣言の model。
    assert verdict.model == "claude-test"
    assert math.isclose(verdict.answer("plain_where").confidence, 0.625)
