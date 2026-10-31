"""The OpenAI client, the pricing table and the token counters are inside doeff-openai.

ADR-DOE-API-001 R1: the import root shows effects, handlers and their types only. The client class
is built inside the handlers from Reader keys, so a caller never constructs ``OpenAIClient`` to set
the timeout or the retries.
"""

import doeff_openai
import openai
import pytest
from _runner import run_program
from doeff_openai._client import OpenAIClient, get_openai_client

INNER_NAMES = (
    "ClientHolder",
    "OpenAIClient",
    "MODEL_PRICING",
    "ModelPricing",
    "calculate_cost",
    "count_embedding_tokens",
    "count_message_tokens",
    "count_tokens",
    "estimate_cost",
    "estimate_max_cost",
    "extract_request_id",
    "extract_token_usage",
    "get_encoding",
    "get_model_pricing",
    "is_gpt5_model",
    "requires_max_completion_tokens",
)


@pytest.mark.parametrize("name", INNER_NAMES)
def test_the_import_root_does_not_show_the_inner_name(name: str) -> None:
    assert name not in doeff_openai.__all__
    with pytest.raises(AttributeError):
        getattr(doeff_openai, name)


@pytest.mark.asyncio
async def test_the_handler_builds_the_client_from_the_reader_settings() -> None:
    env = {
        "openai_api_key": "sk-test",
        "openai_organization": "org-test",
        "openai_base_url": "https://proxy.example/v1",
        "openai_timeout_seconds": 12.5,
        "openai_max_retries": 0,
    }

    result = await run_program(get_openai_client(), env=env)

    assert result.is_ok(), result.error
    client = result.value
    assert isinstance(client, OpenAIClient)
    assert (client.api_key, client.organization, client.base_url) == (
        "sk-test",
        "org-test",
        "https://proxy.example/v1",
    )
    assert (client.timeout, client.max_retries) == (12.5, 0)
    assert client.sync_client.timeout == 12.5
    assert client.sync_client.max_retries == 0


@pytest.mark.asyncio
async def test_missing_settings_leave_the_openai_defaults() -> None:
    result = await run_program(get_openai_client(), env={"openai_api_key": "sk-test"})

    assert result.is_ok(), result.error
    client = result.value
    assert (client.organization, client.base_url, client.timeout) == (None, None, None)
    assert client.max_retries == openai.DEFAULT_MAX_RETRIES
