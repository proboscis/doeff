from dataclasses import dataclass

from doeff import EffectBase, do

@dataclass(frozen=True)
class Fetch(EffectBase):            # an effect: "get the page at url"
    url: str

@dataclass(frozen=True)
class Summarize(EffectBase):        # an effect: "summarize this text"
    text: str

@do
def digest(url):                    # the program: it only yields effects
    page = yield Fetch(url)
    return (yield Summarize(page))

url = "https://example.com"
