"""本番の handler — `Judge` を 1 回きりの print(doeff_claude_code の ClaudePrintOnce)に写して、Claude の CLI に答えさせる。

この module は process を**起こさない**。ClaudePrintOnce を yield するだけで、CLI の process(argv・資格の置き場・待ちの上限・止め方)は
積まれた print の handler(doeff_claude_code の claude-print-handler)が持つ。検は print を fake の handler で答える。

判定 1 回: 問いの部品を作る(prepare — 写せない Judge はここで断る)→ 資格を借りる(BorrowJudgeCredential — 宛先が借りる時だけ)→
print を 1 回出す(待ちの上限 = Judge の timeout_seconds)→ 構造化の答えを Verdict に写す(parse)。失敗はどれも JudgeError で呼び手へ
投げる:

  写せない問い = unsupported / 資格を借りられない = no_credentials / 口座の利用枠が尽きた = usage_limit(JudgeUsageLimited — 枠の種類と
  戻る刻・card ki-eb4f8e1db64d)/ CLI が API の誤りを名乗った = http(status = その HTTP status)/
  CLI が答えずに降りた・待ちの上限を越えた・print の handler の誤り = transport / 答えの形が違う = malformed。

記録は slog("claude_judge", …) 1 行(資格・本文は載せない)。handler は答えを返したら用が済むので Transfer / TransferThrow で続きへ
渡す(doeff-jev と同じ)。
"""

from collections.abc import Callable
from typing import Any

import hy  # noqa: F401  # .hy import hook — 1 回きりの print の effect は Hy の module doeff_claude_code に住む
from doeff_claude_code import (
    ClaudePrintOnce,
    PrintAnswered,
    PrintFailed,
    PrintLimited,
    PrintTimedOut,
)
from doeff_claude_code import Usage
from doeff_claude_code import ClaudeHome, ImageAttachment, LentCredential, PrintContent
from doeff_core_effects import slog
from doeff_hy._frozen import FrozenMap
from doeff_system_one import Judge, JudgeError, JudgeUsageLimited, Verdict

from doeff import Err, Ok, Pass, Transfer, TransferThrow, Try, do
from doeff import handler as _program_handler
from doeff_claude_judge._target import (
    BorrowJudgeCredential,
    ClaudeJudgeTarget,
    JudgeCredential,
    NoJudgeCredential,
)
from doeff_claude_judge._wire import (
    ClaudeQuery,
    MalformedAnswer,
    QueryImage,
    UserPart,
    parse,
    prepare,
)

LOG = "claude_judge"


def lent_credential(credential: JudgeCredential) -> LentCredential:
    """この package の資格の型を、print の effect が運ぶ資格の型に写すため(使い手は doeff_claude_code の型を知らない)。"""
    return LentCredential(access_token=credential.access_token, expires_at=credential.expires_at,
                          subscription_type=credential.subscription_type, rate_limit_tier=credential.rate_limit_tier)


@do
def _performed(effect: Any) -> Any:
    """effect を 1 つ出して答えを受ける Program(Try で包んで、答え手の例外を値で読むため)。"""
    return (yield effect)


def _short(error: BaseException) -> str:
    """例外を断りの文の 1 行にするため(型の名と頭の 120 字)。"""
    return f"{type(error).__name__}: {str(error)[:120]}"


@do
def _borrowed(target: ClaudeJudgeTarget) -> Any:
    """判定 1 回の資格を借りるため。答え = LentCredential(借りた)| None(宛先が借りない)| JudgeError(借りられない)。"""
    if not target.borrow_credential:
        return None
    outcome = yield Try(_performed(BorrowJudgeCredential(model=target.model)))
    if isinstance(outcome, Err):
        return JudgeError("no_credentials", "資格を借りられない: " + _short(outcome.error))
    lent = outcome.value
    if isinstance(lent, NoJudgeCredential):
        return JudgeError("no_credentials", "資格を借りられない: " + lent.detail)
    if not isinstance(lent, JudgeCredential):
        return JudgeError("no_credentials", f"貸し手の答えが資格でない: {type(lent).__name__}")
    return lent_credential(lent)


def _print_error(outcome: PrintFailed | PrintLimited | PrintTimedOut) -> JudgeError:
    """答えの無い print の結末を、Judge の呼び手へ投げる誤りにするため(頭の註の kind の分け方)。"""
    if isinstance(outcome, PrintLimited):
        limit = outcome.limit
        return JudgeUsageLimited(limit.text or "CLI が口座の利用枠の尽きを名乗った", window=limit.window,
                                 resets_at=limit.resets_at)
    if isinstance(outcome, PrintTimedOut):
        return JudgeError("transport", f"CLI が待ちの上限 {outcome.seconds:g} 秒の内に答えなかった")
    if outcome.api_status is not None:
        return JudgeError("http", outcome.detail, status=outcome.api_status)
    return JudgeError("transport", f"{outcome.detail}(終了 code {outcome.exit_code})")


def _input_tokens(usage: Usage) -> int:
    """Verdict の input_tokens(判定器が読んだ token の全部 — cache の作り・読みを含む)を、CLI が名乗った欄だけの和で数えるため。"""
    named = (usage.input_tokens, usage.cache_creation_input_tokens, usage.cache_read_input_tokens)
    return sum(count for count in named if count is not None)


def _answered_model(target: ClaudeJudgeTarget, answered: PrintAnswered) -> str:
    """Verdict の model(答えた model)を決めるため: CLI が 1 つだけ名乗ればそれ・宣言の model を名乗ればそれ・名乗らなければ宣言。"""
    models = answered.models
    if len(models) == 1:
        return models[0]
    if not models or target.model in models:
        return target.model
    return models[0]


def _content_part(part: UserPart) -> str | ImageAttachment:
    """利用者の文の 1 片を、print の本文の片(文字か ImageAttachment)に写すため。"""
    match part:
        case str():
            return part
        case QueryImage(media_type=media_type, data_base64=data):
            return ImageAttachment(mime=media_type, data_base64=data)


def print_input(user: str | tuple[UserPart, ...]) -> str | PrintContent:
    """利用者の文を print の本文に写すため: 文字列はそのまま(素の文字の道)・片の tuple は PrintContent(stream-json の 1 行の道)。"""
    match user:
        case str():
            return user
        case parts:
            return PrintContent(tuple(_content_part(part) for part in parts))


def query_user(content: str | PrintContent) -> str | tuple[UserPart, ...]:
    """print の本文を利用者の文に戻すため(print_input の逆 — 検の fake が宣言から問いの部品を読み直す)。"""
    match content:
        case str():
            return content
        case PrintContent(parts=parts):
            return tuple(part if isinstance(part, str) else QueryImage(media_type=part.mime, data_base64=part.data_base64)
                         for part in parts)


def print_request(target: ClaudeJudgeTarget, judge: Judge, query: ClaudeQuery,
                  credential: LentCredential | None) -> ClaudePrintOnce:
    """Judge 1 つ(とその問いの部品)を、宛先の CLI への 1 回きりの print の宣言に写すため。"""
    return ClaudePrintOnce(home=ClaudeHome(target.config_dir, FrozenMap(dict(target.env))), model=target.model,
                           system_prompt=query.system, json_schema=query.schema_json, input=print_input(query.user),
                           timeout_seconds=judge.timeout_seconds, credential=credential,
                           thinking_tokens=target.thinking_tokens, effort=target.effort)


def _read(target: ClaudeJudgeTarget, judge: Judge, printed: Ok | Err) -> Verdict | JudgeError:
    """print の結末(Try の答え)を Verdict か、呼び手へ投げる誤りにするため(頭の註の kind の分け方)。"""
    if isinstance(printed, Err):
        return JudgeError("transport", "print の handler の誤り: " + _short(printed.error))
    outcome = printed.value
    if not isinstance(outcome, PrintAnswered):
        return _print_error(outcome)
    verdict = parse(judge, outcome.structured_output, model=_answered_model(target, outcome),
                    input_tokens=_input_tokens(outcome.usage), elapsed_ms=outcome.elapsed_ms)
    return verdict.as_error() if isinstance(verdict, MalformedAnswer) else verdict


@do
def _judged(target: ClaudeJudgeTarget, judge: Judge) -> Any:
    """Judge 1 つを判定するため(頭の註の手順)。答え = Verdict | JudgeError(誤りは投げずに値で返す — 投げるのは handler)。"""
    try:
        query = prepare(judge)
    except JudgeError as error:
        return error
    credential = yield _borrowed(target)
    if isinstance(credential, JudgeError):
        return credential
    printed = yield Try(_performed(print_request(target, judge, query, credential)))
    return _read(target, judge, printed)


def claude_judge_handler(target: ClaudeJudgeTarget) -> Callable[[Any], Any]:
    """`Judge` を宛先 `target` の CLI で判定する handler(Program -> Program)。他の effect は Pass。"""

    @do
    def handler(effect: Any, k: Any) -> Any:
        if not isinstance(effect, Judge):
            return (yield Pass(effect, k))
        outcome = yield _judged(target, effect)
        if isinstance(outcome, JudgeError):
            yield slog(LOG, model=target.model, ok=False, kind=outcome.kind, status=outcome.status, error=outcome.detail)
            return (yield TransferThrow(k, outcome))
        yield slog(LOG, model=outcome.model, ok=True, questions=len(effect.questions), tokens=outcome.input_tokens,
                   ms=outcome.elapsed_ms)
        return (yield Transfer(k, outcome))

    return _program_handler(handler)
