"""Question and answer types (plain data, not effects).

A System One judgment is "a question about a state" -> "an answer with probabilities". There are 3 kinds of question:
choice (pick one from the options) / noul (probability that a condition holds) / score (expected value over levels).

The options of a choice are an **id -> description** table (the answer comes back as an id). The levels of a score are an ordered list of descriptions.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

Kind = Literal["choice", "noul", "score"]
KINDS: tuple[str, ...] = ("choice", "noul", "score")


@dataclass(frozen=True)
class Question:
    """One question for the judge.

    options: sequence of (id, description) pairs for choice. levels: sequence of levels for score. noul has neither.
    """

    kind: Kind
    instructions: str
    options: tuple[tuple[str, str], ...] = ()
    levels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of choice / noul / score: {self.kind!r}")
        if self.kind == "choice" and not self.options:
            raise ValueError("choice requires options (id -> description)")
        if self.kind == "score" and not self.levels:
            raise ValueError("score requires levels (a sequence of levels)")
        if self.kind == "noul" and (self.options or self.levels):
            raise ValueError("noul takes neither options nor levels")
        if (self.kind == "choice" and self.levels) or (self.kind == "score" and self.options):
            raise ValueError("choice takes only options, score takes only levels")

    @property
    def option_ids(self) -> list[str]:
        return [option_id for option_id, _ in self.options]

    def as_payload(self) -> dict[str, Any]:
        """TypeSafe wire form (shared by direct and gateway: choice criteria is a record, score is a list)."""
        body: dict[str, Any] = {"type": self.kind, "instructions": self.instructions}
        if self.kind == "choice":
            body["criteria"] = dict(self.options)
        elif self.kind == "score":
            body["criteria"] = list(self.levels)
        return body


IMAGE_FORMATS: tuple[str, ...] = ("png", "jpg", "jpeg")


@dataclass(frozen=True)
class Image:
    """One image referenced by key from the state (TypeSafe images -- https://docs.typesafe.ai/images.md).

    format: png / jpg / jpeg. data_base64: base64 of the file contents (standard alphabet, with = padding, no whitespace and no data: prefix).
    Inside the state, it is referenced by the string key in the Judge assets (e.g. state={"poster": "poster-1"}, assets={"poster-1": Image(...)}).
    """

    format: str
    data_base64: str

    def __post_init__(self) -> None:
        if self.format not in IMAGE_FORMATS:
            raise ValueError(f"format must be one of png / jpg / jpeg: {self.format!r}")
        if not self.data_base64:
            raise ValueError("data_base64 is empty")

    def as_payload(self) -> dict[str, Any]:
        """TypeSafe wire form (value of the root assets in the body)."""
        return {"type": "image", "format": self.format, "data_base64": self.data_base64}


@dataclass(frozen=True)
class Answer:
    """The answer to one question.

    value: choice = id of the chosen option / noul = probability of yes / score = expected value over levels.
    confidence: concentration of the distribution (None when absent). For noul it is substituted by distance from 0.5 x 2 (built by Answer.noul).
    """

    kind: Kind
    value: Any
    confidence: float | None
    probabilities: Mapping[str, float] | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def noul(cls, probability: float, *, probabilities: Mapping[str, float] | None = None,
             raw: Mapping[str, Any] | None = None) -> "Answer":
        """Build a noul answer from the probability of yes (confidence = distance from 0.5 x 2 -- so the judge handler and scripts read the same formula)."""
        return cls(kind="noul", value=probability, confidence=abs(probability - 0.5) * 2,
                   probabilities=probabilities, raw={} if raw is None else raw)


@dataclass(frozen=True)
class Verdict:
    """Result of one judgment (question name -> answer), plus the model, cost and duration the provider reported.

    proxy: origin of the answer reported by the relay (jev-proxy) that returned it (hit / miss / coalesced / refreshed -- the relay's own words).
        None for targets that do not go through a relay, targets that do not report, and judgments that did not call the target.
    cached: answered by the local answer cache (the target was not called). input_tokens and elapsed_ms keep the values from when the question was asked,
        so readers that count cost and wait time exclude cached answers.
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
    """The judgment could not return an answer. kind = no_credentials / http / malformed / transport / unsupported / usage_limit (JudgeUsageLimited)."""

    def __init__(self, kind: str, detail: str, *, status: int | None = None) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail
        self.status = status


class JudgeUsageLimited(JudgeError):
    """The judge account usage limit was exhausted and no answer was given (kind = usage_limit, status = 429).

    Kept distinct from errors of unreachable or broken answers (the other JudgeError kinds), so callers can say "when it can be retried".
    window = kind of the exhausted window (provider's own word, e.g. five_hour, seven_day; None if not reported) /
    resets_at = time the window resets (epoch seconds; None if not reported).
    """

    def __init__(self, detail: str, *, window: str | None, resets_at: int | None) -> None:
        if window is not None and (not isinstance(window, str) or not window):
            raise ValueError(f"JudgeUsageLimited.window must be a non-empty string or None: {window!r}")
        if resets_at is not None and (not isinstance(resets_at, int) or isinstance(resets_at, bool)):
            raise TypeError(f"JudgeUsageLimited.resets_at must be an integer epoch seconds or None: {resets_at!r}")
        super().__init__("usage_limit", detail, status=429)
        self.window = window
        self.resets_at = resets_at

