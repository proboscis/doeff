"""判定の effect。provider を知らない。"""

from collections.abc import Mapping
from typing import Any

from doeff import EffectBase
from doeff_system_one.types import Image, Question


class Judge(EffectBase):
    """state について問いの束を判定器へ出し、`Verdict` を受け取る。

    問いは 1 request に束ねる(独立な問いは同じ state に対して一緒に出す — 順に 1 問ずつ
    出す形は費用と時間の無駄)。`cacheable=False` の判定は答えの cache に載せない。
    `assets` は state から鍵で指す画像(鍵 → `Image` — 画像を読める判定器だけが受ける・既定は空)。
    """

    def __init__(
        self, *,
        state: Any,
        questions: Mapping[str, Question],
        timeout_seconds: float = 4.0,
        cacheable: bool = True,
        assets: Mapping[str, Image] | None = None,
    ) -> None:
        super().__init__()
        if not questions:
            raise ValueError("Judge には問いが 1 つ以上要る")
        for name, question in questions.items():
            if not isinstance(question, Question):
                raise TypeError(f"questions[{name!r}] は Question であること: {type(question).__name__}")
        for name, asset in (assets or {}).items():
            if not isinstance(asset, Image):
                raise TypeError(f"assets[{name!r}] は Image であること: {type(asset).__name__}")
        self.state = state
        self.questions: dict[str, Question] = dict(questions)
        self.timeout_seconds = float(timeout_seconds)
        self.cacheable = bool(cacheable)
        self.assets: dict[str, Image] = dict(assets or {})

    def kinds(self) -> dict[str, str]:
        return {name: question.kind for name, question in self.questions.items()}

    def __repr__(self) -> str:
        return f"Judge(questions={sorted(self.questions)!r})"
