"""claude_judge_handler — Judge を 1 回きりの print(ClaudePrintOnce)へ運ぶ handler。print は fake の handler で答える(CLI を起こさない)。"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import hy  # noqa: F401  # .hy import hook — 1 回きりの print の effect と fake は Hy の module doeff_claude_code に住む
import pytest
from doeff_claude_code._argv import print_once_argv
from doeff_claude_code import (
    ClaudePrintOnce,
    PrintAnswered,
    PrintFailed,
    PrintLimited,
    PrintTimedOut,
)
from doeff_claude_code.fake import fake_claude_print_handler
from doeff_claude_code import AccountLimitHit, Usage
from doeff_claude_code import ImageAttachment, LentCredential, PrintContent
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
from doeff_claude_judge.wire import NOUL_HEAD
from doeff_hy.frozen import freeze_json
from doeff_system_one import Image, Judge, JudgeError, JudgeUsageLimited, choice, noul, score

from doeff import Err, Pass, Resume, ResumeThrow, Try, do
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


def test_a_judge_with_one_picture_prints_one_stream_json_user_line() -> None:
    # 画像 1 枚の判定は、print の本文が PrintContent になり、CLI の argv に --input-format stream-json・標準入力に user の 1 行
    # (開きの印・画像の block・閉じの印と問い — 測り 2026-10-19 の probe_once.py と同じ形)が載る。他の旗は文の判定と同じ。
    # 失敗ケース(前の形): 画像の在る Judge は print の前に unsupported で断られた。
    poster = Judge(state={"poster": "p-1"}, questions={"poster-picture": noul("Is the image `poster` an illustrated picture?")},
                   assets={"p-1": Image(format="png", data_base64="iVBORw0KGgo=")}, timeout_seconds=60.0)
    captured: dict[str, ClaudePrintOnce] = {}
    verdict = _run(poster, printer=_printer(_answered({"poster-picture": 0.875}), captured), lender=_lender(LENT))
    assert verdict.answer("poster-picture").value == 0.875
    request = captured["request"]
    query = prepare(poster)
    assert print_once_argv(("claude",), request) == [
        "claude", "-p", "--output-format", "stream-json", "--verbose", "--input-format", "stream-json",
        "--model", "claude-test", "--system-prompt", query.system, "--json-schema", query.schema_json,
        "--tools", "", "--setting-sources", "", "--strict-mcp-config", "--no-session-persistence", "--effort", "low"]
    # 本文の片(この並びを stdin の 1 行の block の列に綴るのは doeff-claude-code の print-once-stdin — その test が 1 行を確かめる)。
    assert request.input == PrintContent((
        "<poster>", ImageAttachment(mime="image/png", data_base64="iVBORw0KGgo="),
        "</poster>\n\n" + NOUL_HEAD + "\n- poster-picture: Is the image `poster` an illustrated picture?"))


def test_a_judge_without_pictures_prints_its_text_as_before() -> None:
    # 文の判定は素の文字の道のまま: --input-format を付けず、標準入力は利用者の文そのもの。
    captured: dict[str, ClaudePrintOnce] = {}
    _run(JUDGE, printer=_printer(_answered(ANSWER), captured), lender=_lender(LENT))
    request = captured["request"]
    assert "--input-format" not in print_once_argv(("claude",), request)
    assert request.input == prepare(JUDGE).user


def test_the_scripted_printer_reads_back_the_picture_query() -> None:
    poster = Judge(state={"poster": "p-1"}, questions={"n": noul("Is `poster` a picture?")},
                   assets={"p-1": Image(format="jpeg", data_base64="/9j/")})

    def answer(query: ClaudeQuery, model: str) -> JsonIn:
        assert query == prepare(poster)
        return {"n": 0.5}

    assert _run(poster, printer=scripted_printer(answer), lender=fixed_credential_lender(LENT)).answer("n").value == 0.5


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


def test_an_exhausted_usage_limit_is_its_own_refusal_with_the_reset() -> None:
    # 口座の利用枠が尽きた print は、届かない・壊れた答えの誤りと分けた JudgeUsageLimited(枠の種類と戻る刻 — card ki-eb4f8e1db64d)。
    # 失敗ケース(前の形): PrintLimited が無く 429 の http の誤りになり、呼び手は戻る刻を知れない。
    limit = AccountLimitHit(window="five_hour", resets_at=1791342000, text="You've hit your session limit · resets 12pm")
    error = _refusal(JUDGE, printer=_printer(PrintLimited(limit=limit, exit_code=1, stderr_tail=""), {}), lender=_lender(LENT))
    assert isinstance(error, JudgeUsageLimited), error
    assert (error.kind, error.window, error.resets_at) == ("usage_limit", "five_hour", 1791342000)
    assert "session limit" in error.detail


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


def test_the_scripted_printer_answers_a_limited_print_with_the_reset() -> None:
    # 使い手の検の fake の口座の利用枠が尽きた筋書き: limited(問いの部品, model)が尽きの事実を返す print は、CLI が尽きを名乗って
    # 答えずに降りた答えになり、判定は JudgeUsageLimited(枠の種類と戻る刻)で断られる。答えの callback は呼ばれない。
    # limited が None を返す print は今までどおり answer で答える。使い手は尽きの事実の型を doeff_claude_judge から読む
    # (doeff_claude_code を名指さない — wiring の頭の註)。
    # 失敗ケース(前の形): scripted_printer は構造化の答えしか返せず、使い手は doeff_claude_code の ClaudePrintOnce と
    # PrintLimited を名指す自前の handler を足していた。
    from doeff_claude_judge import AccountLimitHit as JudgeLimitHit

    assert JudgeLimitHit is AccountLimitHit
    limit = JudgeLimitHit(window="seven_day", resets_at=1791342000, text="You've hit your weekly limit · resets Oct 25")

    def unanswered(query: ClaudeQuery, model: str) -> JsonIn:
        raise AssertionError(f"尽きた print は答えの callback を呼ばないはず: {model}")

    def spent(query: ClaudeQuery, model: str) -> AccountLimitHit | None:
        assert query == prepare(JUDGE)
        assert model == "claude-test"
        return limit

    error = _refusal(JUDGE, printer=scripted_printer(unanswered, limited=spent), lender=fixed_credential_lender(LENT))
    assert isinstance(error, JudgeUsageLimited), error
    assert (error.window, error.resets_at) == ("seven_day", 1791342000)
    assert "weekly limit" in error.detail
    verdict = _run(JUDGE, printer=scripted_printer(lambda query, model: ANSWER, limited=lambda query, model: None),
                   lender=fixed_credential_lender(LENT))
    assert verdict.answer("plain").value == 0.8125


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
