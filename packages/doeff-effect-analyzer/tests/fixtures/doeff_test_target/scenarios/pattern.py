from doeff import do
from doeff_test_target.core.alpha import helper_alpha
from doeff_test_target.core.beta import helper_beta


@do
def pattern_matcher(kind: str):
    match kind:
        case "alpha":
            value = yield helper_alpha()
        case _:
            value = yield helper_beta()
    return value
