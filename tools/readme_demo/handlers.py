from urllib.request import urlopen

from digest import Fetch, digest, url
from doeff import Pass, Resume, do, handler, run

@do
def canned(effect, k):              # resume k with made-up values
    if isinstance(effect, Fetch):
        return (yield Resume(k, "<h1>Hello</h1>"))
    return (yield Resume(k, f"{len(effect.text)} chars"))

@do
def trace(effect, k):               # see every effect, then pass it on
    print(f"trace: {effect!r}")
    return (yield Pass(effect, k))

def budget(n):                      # let n effects through, then drop k
    @handler
    @do
    def spend(effect, k):
        nonlocal n
        if n == 0:
            return f"over budget at {type(effect).__name__}"
        n -= 1
        return (yield Pass(effect, k))
    return handler(spend)

@do
def live_world(effect: Fetch, k):   # answer Fetch over real HTTP
    return (yield Resume(k, urlopen(effect.url).read().decode()))
