from doeff import Program, do
from doeff_test_target.core.alpha import helper_alpha
from doeff_test_target.core.beta import helper_beta


def dict_builder():
    return Program.dict(alpha=helper_alpha(), beta=helper_beta())


@do
def lifted_alpha():
    program = Program.lift(helper_alpha())
    result = yield program
    return result
