"""Domain effects for OpenAI embedding operations."""


import warnings

from doeff_llm.effects import LLMEmbedding


class Embedding(LLMEmbedding):
    """Deprecated alias of :class:`doeff_llm.effects.LLMEmbedding`.

    Defaults ``model`` to ``text-embedding-3-small`` to preserve the
    previous surface; callers can override via kwargs. Forwards all
    kwargs to the explicit ``LLMEmbedding.__init__`` rather than using
    ``@dataclass`` (which would silently replace the parent constructor
    — see the note in ``chat.py``).
    """

    def __init__(self, *, model: str = "text-embedding-3-small", **kwargs):
        super().__init__(model=model, **kwargs)
        warnings.warn(
            "Embedding is deprecated; use doeff_llm.effects.LLMEmbedding instead.",
            DeprecationWarning,
            stacklevel=2,
        )


__all__ = [
    "Embedding",
]
