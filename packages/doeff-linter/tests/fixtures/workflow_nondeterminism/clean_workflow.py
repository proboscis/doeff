# doeff: workflow
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class PromptFacts:
    timestamp: str
    seed: int


def build_prompt(facts: PromptFacts, params: Mapping[str, str]) -> str:
    return f"{params['task']} at {facts.timestamp} with seed {facts.seed}"
