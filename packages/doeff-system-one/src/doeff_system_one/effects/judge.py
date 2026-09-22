"""判定の effect。provider を知らない。"""

from collections.abc import Mapping
from typing import Any

from doeff import EffectBase
from doeff_system_one.types import Question


class Judge(EffectBase):
    """state について問いの束を判定器へ出し、`Verdict` を受け取る。

    問いは 1 request に束ねる(独立な問いは同じ state に対して一緒に出す — 順に 1 問ずつ
    出す形は費用と時間の無駄)。`cacheable=False` の判定は答えの cache に載せない。
    """

    def __init__(
        self, *,
        state: Any,
        questions: Mapping[str, Question],
        timeout_seconds: float = 4.0,
        cacheable: bool = True,
    ) -> None:
        super().__init__()
        if not questions:
            raise ValueError("Judge には問いが 1 つ以上要る")
        for name, question in questions.items():
            if not isinstance(question, Question):
                raise TypeError(f"questions[{name!r}] は Question であること: {type(question).__name__}")
        self.state = state
        self.questions: dict[str, Question] = dict(questions)
        self.timeout_seconds = float(timeout_seconds)
        self.cacheable = bool(cacheable)

    def kinds(self) -> dict[str, str]:
        return {name: question.kind for name, question in self.questions.items()}

    def __repr__(self) -> str:
        return f"Judge(questions={sorted(self.questions)!r})"
