"""Tests for llm_agent.py: a fake model answers every prompt, so no API key and no network.

Keep this file next to llm_agent.py and run:

    pip install doeff pytest
    pytest test_llm_agent.py
"""

import pytest
from doeff_core_effects import Listen, listen_handler
from llm_agent import (
    QUESTION,
    Complete,
    OverBudgetError,
    budget,
    canned_reply,
    fake_model,
    research,
)

from doeff import run


def test_research_combines_the_answers_to_each_part():
    answer = run(fake_model(canned_reply)(research(QUESTION)))
    assert answer == "No: an existing queue already does all of it, for little cost."


def test_research_asks_one_prompt_per_part_and_passes_the_answers_on():
    # Listen collects the Complete effects the agent performs; fake_model still answers them.
    asking = listen_handler(Listen(research(QUESTION), types=(Complete,)))
    _, asked = run(fake_model(canned_reply)(asking))
    assert [effect.prompt for effect in asked[1:3]] == [
        "Answer in one sentence: What must a job queue do?",
        "Answer in one sentence: What do existing queues cost?",
    ]
    assert asked[3].prompt.endswith(
        "- What must a job queue do? Store jobs, retry failures and survive restarts.\n"
        "- What do existing queues cost? Little: most run on a Redis server you already have."
    )


def test_a_prompt_the_fake_has_no_answer_for_fails_the_test():
    def three_parts(prompt: str) -> str:  # the model splits the question into three parts
        if prompt.startswith("Split into sub-questions"):
            return "What must a job queue do?\nWhat do existing queues cost?\nWho runs it at night?"
        return canned_reply(prompt)

    with pytest.raises(LookupError, match="Who runs it at night"):
        run(fake_model(three_parts)(research(QUESTION)))


def test_budget_stops_an_agent_that_asks_too_often():
    # research asks four times: the plan, two parts, the final answer.
    assert run(fake_model(canned_reply)(budget(4)(research(QUESTION)))).startswith("No:")
    with pytest.raises(OverBudgetError):
        run(fake_model(canned_reply)(budget(3)(research(QUESTION))))
