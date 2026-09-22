"""判定を呼ぶ小さな program。"""

from collections.abc import Iterable, Mapping
from typing import Any

from doeff_core_effects import Gather, Spawn

from doeff import do
from doeff_system_one.effects import Judge
from doeff_system_one.types import Question


@do
def judge(state: Any, questions: Mapping[str, Question], *,
          timeout_seconds: float = 4.0, cacheable: bool = True) -> Any:
    """1 つの state に問いの束を出して Verdict を返す。"""
    verdict = yield Judge(state=state, questions=questions,
                          timeout_seconds=timeout_seconds, cacheable=cacheable)
    return verdict


@do
def judge_many(states: Iterable[Any], questions: Mapping[str, Question], *,
               timeout_seconds: float = 4.0, cacheable: bool = True) -> Any:
    """同じ問いを多数の state に**同時に**出す(順に 1 件ずつは出さない)。順序は入力どおり。

    scheduler(`scheduled`)が積まれた組み立てで走らせる。
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
