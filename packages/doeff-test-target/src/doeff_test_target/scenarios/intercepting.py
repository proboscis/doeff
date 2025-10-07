from doeff import do

from ..core.alpha import helper_alpha


@do
def intercepted_alpha():
    program = helper_alpha()
    result = yield program.intercept(lambda eff: eff)
    return result
