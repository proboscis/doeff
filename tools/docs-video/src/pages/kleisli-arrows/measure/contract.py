# The handler scene: a handler is a @do function too, and @handler holds it to a contract when the handler is
# built (docs/11-kleisli-arrows.md, "@do Handler Authoring Contract"). It must take (effect, k), and its effect
# annotation must name a class an effect can be. A handler that breaks either is refused with a TypeError where
# @handler is applied, before any program runs; the one that keeps it answers Fetch.
from dataclasses import dataclass

from doeff import EffectBase, Resume, do, handler, run


@dataclass(frozen=True)
class Fetch(EffectBase):
    url: str


@do
def page():
    body = yield Fetch("https://example.com")
    return f"body={body}"


print("@handler def no_k(effect: Fetch):")
try:

    @handler
    def no_k(effect: Fetch):
        return "no k"

except TypeError as error:
    print(f"  raises TypeError: {error}")

print("@handler def wrong_type(effect: int, k):")
try:

    @handler
    def wrong_type(effect: int, k):
        return (yield Resume(k, 0))

except TypeError as error:
    print(f"  raises TypeError: {str(error).split(': ', 1)[1]}")

print("@handler def fetcher(effect: Fetch, k):")


@handler
def fetcher(effect: Fetch, k):
    reply = f"got {effect.url}"
    return (yield Resume(k, reply))


print("  built; run(fetcher(page())) ->", repr(run(fetcher(page()))))
