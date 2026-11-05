"""Small programs that call the judgment."""

from collections.abc import Iterable, Mapping
from typing import Any

from doeff_core_effects import Gather, Spawn

from doeff import do
from doeff_system_one._effects import Judge
from doeff_system_one._types import Question


@do
def judge(state: Any, questions: Mapping[str, Question], *,
          timeout_seconds: float = 4.0, cacheable: bool = True) -> Any:
    """Send a bundle of questions for one state and return the Verdict."""
    verdict = yield Judge(state=state, questions=questions,
                          timeout_seconds=timeout_seconds, cacheable=cacheable)
    return verdict


@do
def judge_many(states: Iterable[Any], questions: Mapping[str, Question], *,
               timeout_seconds: float = 4.0, cacheable: bool = True) -> Any:
    """Send the same questions for many states **concurrently** (not one by one). Order follows the input.

    Run it in an assembly with a scheduler (`scheduled`) stacked.
    """
    tasks = []
    for state in states:
        task = yield Spawn(judge(state, questions, timeout_seconds=timeout_seconds,
                                 cacheable=cacheable))
        tasks.append(task)
    if not tasks:
        return []
    verdicts = yield Gather(*tasks)
    return list(verdicts)
