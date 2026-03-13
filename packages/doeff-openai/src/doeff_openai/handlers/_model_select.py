"""Shared OpenAI model routing helpers used by production and test handlers."""

OPENAI_MODEL_PREFIXES = ("gpt-", "o1-", "o3-", "o4-", "text-embedding-")
OPENAI_MODEL_EXCLUSIONS = ("text-embedding-004", "embedding-001")


def _is_openai_model(model: str) -> bool:
    if model in OPENAI_MODEL_EXCLUSIONS:
        return False
    return any(model.startswith(prefix) for prefix in OPENAI_MODEL_PREFIXES)


__all__ = [
    "OPENAI_MODEL_EXCLUSIONS",
    "OPENAI_MODEL_PREFIXES",
    "_is_openai_model",
]
