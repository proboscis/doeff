"""The judgment effect. Unaware of the provider."""

from collections.abc import Mapping
from typing import Any

from doeff import EffectBase
from doeff_system_one._types import Image, Question, Verdict


class Judge(EffectBase[Verdict]):
    """Send a bundle of questions about the state to the judge and receive a `Verdict`.

    Questions are bundled into 1 request (independent questions are sent together for the same state -- sending them one at a time
    wastes cost and time). A judgment with `cacheable=False` is not stored in the answer cache.
    `assets` are images referenced by key from the state (key -> `Image` -- only judges that can read images accept them; default empty).
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
            raise ValueError("Judge requires at least one question")
        for name, question in questions.items():
            if not isinstance(question, Question):
                raise TypeError(f"questions[{name!r}] must be a Question: {type(question).__name__}")
        for name, asset in (assets or {}).items():
            if not isinstance(asset, Image):
                raise TypeError(f"assets[{name!r}] must be an Image: {type(asset).__name__}")
        self.state = state
        self.questions: dict[str, Question] = dict(questions)
        self.timeout_seconds = float(timeout_seconds)
        self.cacheable = bool(cacheable)
        self.assets: dict[str, Image] = dict(assets or {})

    def kinds(self) -> dict[str, str]:
        return {name: question.kind for name, question in self.questions.items()}

    def __repr__(self) -> str:
        return f"Judge(questions={sorted(self.questions)!r})"
