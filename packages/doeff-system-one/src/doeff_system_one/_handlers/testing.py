"""Test handler -- returns the scripted Verdict and records the calls. No network."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from doeff import Pass, Transfer, TransferThrow, do
from doeff import handler as _program_handler
from doeff_system_one._effects import Judge
from doeff_system_one._types import Answer, JudgeError, Verdict

Script = Callable[[Judge], Verdict | JudgeError] | Mapping[str, Answer] | Verdict


@dataclass
class ScriptedJudgeState:
    """Record: the sequence of Judges issued (state and question names)."""

    calls: list[Judge] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.calls)


def _resolve(script: Script, effect: Judge) -> Verdict | JudgeError:
    if isinstance(script, Verdict | JudgeError):
        return script
    if callable(script):
        return script(effect)
    missing = [name for name in effect.questions if name not in script]
    if missing:
        return JudgeError("malformed", f"scripted answers missing: {missing}")
    return Verdict(answers={name: script[name] for name in effect.questions},
                   model="scripted", input_tokens=0, elapsed_ms=0)


def scripted_judge_handler(script: Script, state: ScriptedJudgeState | None = None) -> Callable[[Any], Any]:
    """Handler (Program -> Program) that answers `Judge` from a script.

    script takes 3 forms: a table of question name -> Answer / a fixed Verdict / a function `Judge -> Verdict | JudgeError`.
    A script that returns a JudgeError throws that exception to the caller of the Judge.
    """
    record = state if state is not None else ScriptedJudgeState()

    @do
    def handler(effect: Any, k: Any) -> Any:
        if not isinstance(effect, Judge):
            return (yield Pass(effect, k))
        record.calls.append(effect)
        outcome = _resolve(script, effect)
        if isinstance(outcome, JudgeError):
            return (yield TransferThrow(k, outcome))
        return (yield Transfer(k, outcome))

    return _program_handler(handler)
