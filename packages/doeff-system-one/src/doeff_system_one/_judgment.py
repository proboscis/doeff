"""Judgment tools (pure, unaware of the network).

How to read an answer is up to the caller, but shapes stepped on repeatedly are written here once.
"""

from collections.abc import Callable

from doeff_system_one._types import Answer

#: Advised size (chars) of the text put in the state. Beyond it confidence drops (jevadr measurement 2026-09-19: a window over 2,000 chars
#: does not lower the score; confidence becomes 0 = the judge cannot give an answer).
STATE_CHARS_ADVISED = 2_000


def answered(answer: Answer | None, floor: float) -> bool:
    """Whether an answer is given. **No confidence / below the floor = cannot judge** (neither pass nor violation)."""
    return (answer is not None and answer.confidence is not None
            and answer.confidence >= floor)


def crosses(answer: Answer | None, *, value_floor: float, confidence_floor: float) -> bool:
    """Whether both value and confidence exceed the floor (for score / noul). False if no answer is given."""
    if not answered(answer, confidence_floor):
        return False
    assert answer is not None
    try:
        return float(answer.value) >= value_floor
    except (TypeError, ValueError):
        return False


def chosen(answer: Answer | None, *, floor: float, none_label: str | None = None) -> str | None:
    """Fold a choice answer into "take / do not take". None if confidence is below the floor or the answer is "none of them"."""
    if not answered(answer, floor):
        return None
    assert answer is not None
    value = answer.value
    if value is None or (none_label is not None and value == none_label):
        return None
    return str(value)


def calibration_ok(bad: Answer | None, good: Answer | None,
                   crosses_fn: Callable[[Answer], bool], floor: float) -> bool:
    """Measure the judge before use. The violating example (bad) must cross, the compliant example (good) must not cross, and **both must
    actually give an answer**. With only "pass if the positive example does not cross the floor", a judge that returns "cannot judge"
    for anything passes calibration (jevadr live fire 2026-09-19)."""
    return (answered(bad, floor) and answered(good, floor)
            and bool(crosses_fn(bad)) and not bool(crosses_fn(good)))  # type: ignore[arg-type]


def clip(text: object, limit: int = STATE_CHARS_ADVISED) -> str:
    """Cut the text put in the state at the limit (keep the head)."""
    if text is None:
        return ""
    value = str(text)
    return value if len(value) <= limit else value[:limit]
