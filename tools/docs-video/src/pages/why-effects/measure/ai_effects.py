# The running example of docs/20-why-effects-over-di.md, as the page gives it: the other runs here import it.
from dataclasses import dataclass

from doeff import EffectBase, Resume, do


@dataclass(frozen=True)
class Chat(EffectBase):       # "ask a language model"
    model: str
    prompt: str


@dataclass(frozen=True)
class Draw(EffectBase):       # "ask an image model"
    model: str
    prompt: str


@do
def illustrated_summary(topic):
    summary = yield Chat("gpt-4o", f"Summarize {topic}")
    picture = yield Draw("imagen-3", summary)
    return f"{summary} | {picture}"


@do
def canned_chat(effect: Chat, k):     # the annotation picks the effects it answers
    return (yield Resume(k, f"summary by {effect.model}"))


@do
def canned_images(effect: Draw, k):
    return (yield Resume(k, f"image by {effect.model}"))
