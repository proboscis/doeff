"""問いと答えの型(素のデータ・effect ではない)。

System One の判定は「state に対する問い」→「確率つきの答え」で、問いは 3 種類:
choice(候補から 1 つ)/ noul(条件が成り立つ確率)/ score(段階の期待値)。

choice の候補は **id → 説明** の表(答えは id で返る)。score の段階は順序つきの説明の列。
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

Kind = Literal["choice", "noul", "score"]
KINDS: tuple[str, ...] = ("choice", "noul", "score")


@dataclass(frozen=True)
class Question:
    """判定器への問い 1 つ。

    options: choice の候補(id, 説明)の対の列。levels: score の段階の列。noul はどちらも持たない。
    """

    kind: Kind
    instructions: str
    options: tuple[tuple[str, str], ...] = ()
    levels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"kind は choice / noul / score のどれか: {self.kind!r}")
        if self.kind == "choice" and not self.options:
            raise ValueError("choice は候補(id → 説明)が要る")
        if self.kind == "score" and not self.levels:
            raise ValueError("score は段階の列が要る")
        if self.kind == "noul" and (self.options or self.levels):
            raise ValueError("noul は候補も段階も持たない")
        if (self.kind == "choice" and self.levels) or (self.kind == "score" and self.options):
            raise ValueError("choice は options だけ、score は levels だけを持つ")

    @property
    def option_ids(self) -> list[str]:
        return [option_id for option_id, _ in self.options]

    def as_payload(self) -> dict[str, Any]:
        """TypeSafe の綴り(direct・gateway 共通の中身: choice の criteria は record、score は列)。"""
        body: dict[str, Any] = {"type": self.kind, "instructions": self.instructions}
        if self.kind == "choice":
            body["criteria"] = dict(self.options)
        elif self.kind == "score":
            body["criteria"] = list(self.levels)
        return body


IMAGE_FORMATS: tuple[str, ...] = ("png", "jpg", "jpeg")


@dataclass(frozen=True)
class Image:
    """state から鍵で指す画像 1 枚(TypeSafe の images — https://docs.typesafe.ai/images.md)。

    format: png / jpg / jpeg。data_base64: file の中身の base64(標準の綴り・= の詰めつき・空白も data: の頭も無い)。
    state の中では、Judge の assets の鍵の文字列で指す(例 state={"poster": "poster-1"}・assets={"poster-1": Image(...)})。
    """

    format: str
    data_base64: str

    def __post_init__(self) -> None:
        if self.format not in IMAGE_FORMATS:
            raise ValueError(f"format は png / jpg / jpeg のどれか: {self.format!r}")
        if not self.data_base64:
            raise ValueError("data_base64 が空")

    def as_payload(self) -> dict[str, Any]:
        """TypeSafe の綴り(本文の根の assets の値)。"""
        return {"type": "image", "format": self.format, "data_base64": self.data_base64}


@dataclass(frozen=True)
class Answer:
    """問い 1 つへの答え。

    value: choice = 選ばれた候補の id / noul = はいの確率 / score = 段階の期待値。
    confidence: 分布の集中度(無い時は None)。noul は 0.5 からの距離 × 2 で代用する。
    """

    kind: Kind
    value: Any
    confidence: float | None
    probabilities: Mapping[str, float] | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Verdict:
    """1 回の判定の結果(問いの名前 → 答え)と、provider が名乗った model・費用・所要。

    proxy: 答えを返した中継(jev-proxy)が名乗った答えの出所(hit / miss / coalesced / refreshed — 中継の語のまま)。
      中継を通らない宛先・名乗らない宛先・宛先を呼ばなかった判定は None。
    cached: 手元の答えの cache が答えた(宛先を呼んでいない)。input_tokens・elapsed_ms は答えを問うた時の値のままなので、
      費用と待ち時間を数える読み手は cached の答えを除く。
    """

    answers: Mapping[str, Answer]
    model: str = ""
    input_tokens: int = 0
    elapsed_ms: int = 0
    proxy: str | None = None
    cached: bool = False

    def answer(self, name: str) -> Answer | None:
        return self.answers.get(name)


class JudgeError(Exception):
    """判定が答えを返せなかった。kind = no_credentials / http / malformed / transport / unsupported / usage_limit(JudgeUsageLimited)。"""

    def __init__(self, kind: str, detail: str, *, status: int | None = None) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail
        self.status = status


class JudgeUsageLimited(JudgeError):
    """判定器の口座の利用枠が尽きて答えなかった(kind = usage_limit・status = 429)。

    届かない・壊れた答えの誤り(JudgeError の他の kind)と分けて、呼び手が「いつ出し直せるか」を言うため。
    window = 尽きた枠の種類(provider の語のまま — 例 five_hour・seven_day。名乗らなければ None)/
    resets_at = 枠が戻る刻(epoch 秒・名乗らなければ None)。
    """

    def __init__(self, detail: str, *, window: str | None, resets_at: int | None) -> None:
        if window is not None and (not isinstance(window, str) or not window):
            raise ValueError(f"JudgeUsageLimited.window は空でない文字列か None: {window!r}")
        if resets_at is not None and (not isinstance(resets_at, int) or isinstance(resets_at, bool)):
            raise TypeError(f"JudgeUsageLimited.resets_at は epoch 秒の整数か None: {resets_at!r}")
        super().__init__("usage_limit", detail, status=429)
        self.window = window
        self.resets_at = resets_at


def answer_choice(value: str, confidence: float | None = 1.0,
                  probabilities: Mapping[str, float] | None = None) -> Answer:
    return Answer(kind="choice", value=value, confidence=confidence, probabilities=probabilities)


def answer_noul(probability: float) -> Answer:
    return Answer(kind="noul", value=probability, confidence=abs(probability - 0.5) * 2)


def answer_score(value: float, confidence: float | None = 1.0) -> Answer:
    return Answer(kind="score", value=value, confidence=confidence)
