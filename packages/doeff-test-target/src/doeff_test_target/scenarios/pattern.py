from doeff import do

from ..core.alpha import helper_alpha
from ..core.beta import helper_beta


@do
def pattern_matcher(kind: str):
    match kind:
        case "alpha":
            value = yield helper_alpha()
        case _:
            value = yield helper_beta()
    return value
