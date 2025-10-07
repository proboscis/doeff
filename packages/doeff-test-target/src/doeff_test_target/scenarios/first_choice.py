from doeff import Program, do
from doeff.types import Maybe

from ..core.alpha import helper_alpha
from ..core.beta import helper_beta


@do
def choose_first_success():
    result = yield Program.first_success(
        helper_alpha(),
        helper_beta(),
    )
    return result


@do
def choose_first_some():
    result = yield Program.first_some(
        helper_beta().map(Maybe.some),
        Program.lift(Maybe.none()),
        helper_alpha().map(Maybe.some),
    )
    return result
