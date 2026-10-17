from dataclasses import dataclass
from urllib.request import urlopen

from doeff import EffectBase, Resume, do, handler, run

@dataclass(frozen=True)
class Fetch(EffectBase):            # a request your code can make
    url: str

@do
def digest(url):                    # your logic: no I/O inside
    page = yield Fetch(url)
    return f"{len(page)} chars"

@do
def fake_world(effect: Fetch, k):   # answers Fetch with a canned page
    return (yield Resume(k, "<h1>Hello</h1>"))

@do
def live_world(effect: Fetch, k):   # answers Fetch over real HTTP
    return (yield Resume(k, urlopen(effect.url).read().decode()))

url = "https://example.com"
