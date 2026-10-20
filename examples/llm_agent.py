"""An LLM agent whose model calls are effects: test it with canned answers, run it on a model.

The agent never calls a client. It yields `Complete(prompt)`, and a handler decides who answers:
canned answers in tests, an OpenAI-compatible client in production.

Put this file and its tests, test_llm_agent.py, in a directory of your own. They need only doeff,
and the tests pytest besides:

    curl -O https://raw.githubusercontent.com/proboscis/doeff/main/examples/llm_agent.py
    curl -O https://raw.githubusercontent.com/proboscis/doeff/main/examples/test_llm_agent.py
    pip install doeff pytest
    python llm_agent.py        # canned answers: no API key, no network
    pytest test_llm_agent.py   # tests that use the same canned answers

To ask a real model instead, `pip install openai` and name the model with `--live`. The client
reads OPENAI_API_KEY, and OPENAI_BASE_URL when you point it at another OpenAI-compatible server
(a local Ollama is http://localhost:11434/v1):

    python llm_agent.py --live gpt-4o-mini
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass

from doeff import EffectBase, Pass, Resume, do, handler, run


@dataclass(frozen=True)
class Complete(EffectBase[str]):  # a request your code can make: "complete this prompt"
    prompt: str


@do
def research(question: str):  # the agent: split the question, answer each part, combine
    plan = yield Complete(f"Split into sub-questions, one per line: {question}")
    parts = [line.strip("-* ") for line in plan.splitlines() if line.strip("-* ")]
    notes: tuple[str, ...] = ()
    for part in parts:
        answer = yield Complete(f"Answer in one sentence: {part}")
        notes = (*notes, f"- {part} {answer}")
    return (
        yield Complete(f"Combine the notes into one answer to: {question}\n" + "\n".join(notes))
    )


def openai_chat(model: str):
    """Production: answer each Complete with a chat completion from an OpenAI-compatible server."""
    from openai import OpenAI  # pip install openai

    client = OpenAI()  # reads OPENAI_API_KEY, and OPENAI_BASE_URL when it is set

    @handler
    @do
    def ask_model(effect: Complete, k):
        response = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": effect.prompt}]
        )
        text = response.choices[0].message.content
        if text is None:  # a refusal or a tool call: no text to answer Complete with
            raise ValueError(f"{model} sent no text for the prompt {effect.prompt!r}")
        return (yield Resume(k, text))

    return handler(ask_model)


def fake_model(reply: Callable[[str], str]):
    """Tests: answer each Complete with reply(prompt). No client, no key, no network."""

    @handler
    @do
    def answer(effect: Complete, k):
        return (yield Resume(k, reply(effect.prompt)))

    return handler(answer)


class OverBudgetError(Exception):
    """The agent asked the model more often than its budget allows."""


def budget(max_calls: int):
    """Production and tests: let at most max_calls prompts through, whoever answers them."""
    calls = 0

    @handler
    @do
    def count(effect: Complete, k):
        nonlocal calls
        calls += 1
        if calls > max_calls:
            raise OverBudgetError(f"the agent asked the model more than {max_calls} times")
        return (yield Pass(effect, k))  # the next handler out answers it

    return handler(count)


QUESTION = "Should a small team build its own job queue?"


def canned_reply(prompt: str) -> str:
    """What a model might answer to each prompt `research(QUESTION)` sends."""
    if prompt.startswith("Split into sub-questions"):
        return "What must a job queue do?\nWhat do existing queues cost?"
    if prompt == "Answer in one sentence: What must a job queue do?":
        return "Store jobs, retry failures and survive restarts."
    if prompt == "Answer in one sentence: What do existing queues cost?":
        return "Little: most run on a Redis server you already have."
    if prompt.startswith("Combine the notes"):
        return "No: an existing queue already does all of it, for little cost."
    raise LookupError(f"no canned answer for the prompt {prompt!r}")


def main() -> None:
    """Run the agent once: on canned answers, or with `--live MODEL` on a real model."""
    parser = argparse.ArgumentParser(description="Run the research agent once.")
    parser.add_argument("--live", metavar="MODEL", help="ask MODEL instead of canned answers")
    live = parser.parse_args().live
    model = fake_model(canned_reply) if live is None else openai_chat(live)
    # budget is inside model, so it sees each Complete first and passes it on.
    answer = run(model(budget(10)(research(QUESTION))))
    print(f"question: {QUESTION}")
    print(f"answer: {answer}")


if __name__ == "__main__":
    main()
