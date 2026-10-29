from doeff import do
from doeff_test_target.core.alpha import helper_alpha


@do
def intercepted_alpha():
    program = helper_alpha()
    result = yield program.intercept(lambda eff: eff)
    return result
