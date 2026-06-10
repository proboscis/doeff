from dataclasses import dataclass
from typing import Mapping

from doeff_conductor.dsl import artifact, defworkflow


@dataclass(frozen=True)
class PromptFacts:
    timestamp: str
    seed: int


def build_prompt(facts: PromptFacts, params: Mapping[str, str]) -> str:
    return f"{params['task']} at {facts.timestamp} with seed {facts.seed}"


WORKFLOW = defworkflow(
    "clean-workflow",
    params={},
    roles={},
    body=[artifact("ok")],
)
