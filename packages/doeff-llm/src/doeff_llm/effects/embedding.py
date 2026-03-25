"""Provider-agnostic embedding effects."""

from doeff import EffectBase


class LLMEmbedding(EffectBase):
    """Request provider-agnostic embeddings."""

    def __init__(self, *, input: str | list[str], model: str):
        super().__init__()
        self.input = input
        self.model = model

    def __repr__(self):
        return f"LLMEmbedding(model={self.model!r})"


__all__ = [
    "LLMEmbedding",
]
