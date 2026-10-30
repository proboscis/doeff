"""判定の宛先と借りた資格の型 — 使い手が doeff_claude_code の型を知らずに Judge を Claude へ運ぶための、この package の型。

- `ClaudeJudgeTarget`: どの model で、どの家(設定 dir と子の env)の CLI に判定させるか。
- `JudgeCredential`: 判定 1 回のために借りた Claude の口座の資格(access token と期限 — refresh token は運ばない)。
- `BorrowJudgeCredential`: 判定 1 回ごとに資格を借りる effect。使い手の組み立て(貸し手の handler)が答える。

資格の値は repr にも断りの文にも出さない(例外・log に写らない)。
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from doeff import EffectBase

#: 資格の文字列に入れてはいけない字(file の 1 行の値として壊れる字)。
FORBIDDEN_CHARS: tuple[str, ...] = ("\r", "\n", "\x00")


def _one_line(value: object, name: str, *, optional: bool) -> None:
    """文字列の欄が空でない 1 行(optional なら None も)である事を、値を断りの文に写さずに確かめるため。"""
    if optional and value is None:
        return
    if not isinstance(value, str) or not value or any(char in value for char in FORBIDDEN_CHARS):
        raise ValueError(f"{name} は空でない 1 行の文字列{'か None' if optional else ''}")


@dataclass(frozen=True, kw_only=True)
class JudgeCredential:
    """判定 1 回のために借りた Claude の口座の資格。

    access_token = 上流へ送る access token(refresh token は運ばない — 更新は貸し手だけが行う)/ expires_at = その token を使って
    よい期限(epoch 秒・None = 知らない)/ subscription_type・rate_limit_tier = 貸し手が読んだ契約の種類と階級(None = 知らない)。
    """

    access_token: str = field(repr=False)
    expires_at: float | None
    subscription_type: str | None = None
    rate_limit_tier: str | None = None

    def __post_init__(self) -> None:
        _one_line(self.access_token, "JudgeCredential.access_token", optional=False)
        _one_line(self.subscription_type, "JudgeCredential.subscription_type", optional=True)
        _one_line(self.rate_limit_tier, "JudgeCredential.rate_limit_tier", optional=True)
        if self.expires_at is not None and (isinstance(self.expires_at, bool)
                                            or not isinstance(self.expires_at, int | float)
                                            or not math.isfinite(self.expires_at)):
            raise TypeError("JudgeCredential.expires_at は epoch 秒の有限の数か None")


@dataclass(frozen=True)
class NoJudgeCredential:
    """判定のための資格を借りられなかった(貸し手の答え)。detail = 借りられなかった訳の 1 行(資格の値は書かない)。"""

    detail: str


class BorrowJudgeCredential(EffectBase[JudgeCredential | NoJudgeCredential]):
    """判定 1 回のために Claude の口座の資格を借りる。答え = `JudgeCredential` | `NoJudgeCredential`。

    model = 判定させる model(貸し手が借りる口座や持ち時間を選ぶ手掛かり)。貸し手の handler は使い手の組み立てが積む
    (例: 短い持ち時間で貸す預かりの service へ借りに行く)。貸し手が例外を投げても、判定の handler は借りられなかったと読む。
    """

    def __init__(self, *, model: str) -> None:
        super().__init__()
        self.model = model

    def __repr__(self) -> str:
        return f"BorrowJudgeCredential(model={self.model!r})"


@dataclass(frozen=True, kw_only=True)
class ClaudeJudgeTarget:
    """Judge を答えさせる Claude の宛先。

    model = CLI の --model / config_dir = 家の設定 dir(借りた資格はこの下の、その process だけの設定 dir に置く — 判定だけの空の
    dir を渡すと、家の CLAUDE.md・hook・plugin を CLI に見せない)/ env = 子の CLI の env ちょうど(PATH・HOME など — handler は
    os.environ を足さない。資格を置かない)/ thinking_tokens = 子の env の MAX_THINKING_TOKENS(0 = 考えない。None = CLI の既定)/
    effort = --effort(None = CLI の既定)/ borrow_credential = 判定ごとに BorrowJudgeCredential で資格を借りるか(偽 = 家の設定 dir の
    資格のまま — 手元の機体で走らせる時)。待ちの上限は Judge の timeout_seconds(CLI の起動と終わりの秒を含む)。
    """

    model: str
    config_dir: str
    env: Mapping[str, str] = field(default_factory=dict)
    thinking_tokens: int | None = None
    effort: str | None = None
    borrow_credential: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model:
            raise ValueError("ClaudeJudgeTarget.model は空でない文字列")
        if not isinstance(self.config_dir, str) or not self.config_dir:
            raise ValueError("ClaudeJudgeTarget.config_dir は空でない文字列")
        if not isinstance(self.env, Mapping) or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in self.env.items()):
            raise TypeError("ClaudeJudgeTarget.env は文字列 → 文字列の写像")
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))
        if self.thinking_tokens is not None and (isinstance(self.thinking_tokens, bool)
                                                 or not isinstance(self.thinking_tokens, int)
                                                 or self.thinking_tokens < 0):
            raise ValueError("ClaudeJudgeTarget.thinking_tokens は 0 以上の整数か None")
        if self.effort is not None and (not isinstance(self.effort, str) or not self.effort):
            raise ValueError("ClaudeJudgeTarget.effort は空でない文字列か None")
        if not isinstance(self.borrow_credential, bool):
            raise TypeError("ClaudeJudgeTarget.borrow_credential は真偽値")
