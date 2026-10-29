from doeff import Program, do
from doeff_test_target.core.alpha import helper_alpha
from doeff_test_target.core.beta import helper_beta


@do
def comprehension_effects():
    programs = [helper_alpha(), helper_beta()]
    results = yield Program.list(*programs)
    return results
