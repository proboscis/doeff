"""問いと答えの型(素のデータ・effect ではない)。

System One の判定は「state に対する問い」→「確率つきの答え」で、問いは 3 種類:
choice(候補から 1 つ)/ noul(条件が成り立つ確率)/ score(段階の期待値)。

choice の候補は **id → 説明** の表(答えは id で返る)。score の段階は順序つきの説明の列。
"""

from collections.abc import Mapping, Sequence
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


def choice(instructions: str, options: Mapping[str, str] | Sequence[str]) -> Question:
    """候補から 1 つ選ぶ問い。候補は id → 説明の表か、id だけの列(説明 = id)。
    「どれでもない」を候補に含めたい時は呼び手が足す。"""
    if isinstance(options, Mapping):
        pairs = tuple((str(k), str(v)) for k, v in options.items())
    else:
        pairs = tuple((str(o), str(o)) for o in options)
    return Question(kind="choice", instructions=instructions, options=pairs)


def noul(instructions: str) -> Question:
    """条件が成り立つ確率を問う。"""
    return Question(kind="noul", instructions=instructions)


def score(instructions: str, levels: Sequence[str]) -> Question:
    """段階(順序つき)の期待値を問う。"""
    return Question(kind="score", instructions=instructions, levels=tuple(str(level) for level in levels))


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
    """1 回の判定の結果(問いの名前 → 答え)と、provider が名乗った model・費用・所要。"""

    answers: Mapping[str, Answer]
    model: str = ""
    input_tokens: int = 0
    elapsed_ms: int = 0

    def answer(self, name: str) -> Answer | None:
        return self.answers.get(name)


class JudgeError(Exception):
    """判定が答えを返せなかった。kind = no_credentials / http / malformed / transport。"""

    def __init__(self, kind: str, detail: str, *, status: int | None = None) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail
        self.status = status


def answer_choice(value: str, confidence: float | None = 1.0,
                  probabilities: Mapping[str, float] | None = None) -> Answer:
    return Answer(kind="choice", value=value, confidence=confidence, probabilities=probabilities)


def answer_noul(probability: float) -> Answer:
    return Answer(kind="noul", value=probability, confidence=abs(probability - 0.5) * 2)


def answer_score(value: float, confidence: float | None = 1.0) -> Answer:
    return Answer(kind="score", value=value, confidence=confidence)
