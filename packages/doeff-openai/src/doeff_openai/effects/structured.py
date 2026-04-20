"""Domain effects for OpenAI structured output operations."""


import warnings

from doeff_llm.effects import LLMStructuredQuery


class StructuredOutput(LLMStructuredQuery):
    """Deprecated alias of :class:`doeff_llm.effects.LLMStructuredQuery`.

    Forwards kwargs to the parent's explicit ``__init__``; see the note
    in ``chat.py`` for why ``@dataclass`` cannot be used here.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        warnings.warn(
            "StructuredOutput is deprecated; use doeff_llm.effects.LLMStructuredQuery instead.",
            DeprecationWarning,
            stacklevel=2,
        )


__all__ = [
    "StructuredOutput",
]
