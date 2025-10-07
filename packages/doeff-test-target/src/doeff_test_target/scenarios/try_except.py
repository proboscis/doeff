from doeff import do

from ..core.alpha import helper_alpha


@do
def try_except_yield():
    try:
        value = yield helper_alpha()
    except Exception:
        value = "fallback"
    return value
