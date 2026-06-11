"""Cost calculation effect for OpenAI API responses.

Lets the pricing table live behind a handler so a user can install their
own (for internal price overrides, future models, cached-input billing,
etc.) without editing ``MODEL_PRICING``. The default handler supplied by
:mod:`doeff_openai.handlers.production` knows about the built-in models;
anything unknown propagates via ``Pass`` so an outer handler has a chance,
and an unhandled effect becomes a loud ``RuntimeError``.
"""


from dataclasses import dataclass

from doeff import EffectBase
from doeff_openai.types import TokenUsage


@dataclass(frozen=True)
class CalculateCost(EffectBase):
    """Compute the billed cost of an OpenAI API response.

    A handler resolves this by reading pricing (per-1k token rates for
    input / cached-input / output) and returning a ``CostInfo``. When the
    handler does not have pricing for the requested model, it must
    ``yield Pass(effect, k)`` so a caller-installed handler can
    substitute — never silently fall back to a different model's rate.
    """

    model: str
    token_usage: TokenUsage


__all__ = ["CalculateCost"]
