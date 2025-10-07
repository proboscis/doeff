from doeff import Program, do
from doeff.effects import log

from .core.alpha import helper_alpha
from .core.beta import helper_beta
from .core.gamma import helper_gamma
from .core.delta import helper_delta
from .core.epsilon import helper_epsilon
from .core.zeta import helper_zeta
from .combinators.advanced import (
    helper_eta,
    helper_theta,
    helper_iota,
    helper_kappa,
)


@do
def orchestrate():
    part_one = yield Program.list(
        helper_alpha(),
        helper_beta(),
        helper_gamma(),
        helper_delta(),
    )

    part_two = yield Program.tuple(
        helper_epsilon(),
        helper_zeta().flat_map(lambda _: helper_eta()),
        helper_theta(),
    )

    part_three = yield Program.set(
        helper_iota(),
        helper_kappa(),
    )

    mapped = yield Program.dict(
        alpha=helper_alpha(),
        beta=helper_beta().map(lambda data: data + "-mapped"),
        gamma=Program.sequence([helper_zeta(), helper_eta()]),
    )

    extra = yield Program.list(
        helper_theta(),
        helper_iota().flat_map(lambda _: helper_alpha()),
        Program.dict(inner=Program.list(helper_beta(), helper_gamma())),
    )

    yield log("orchestrate")
    return part_one, part_two, part_three, mapped, extra
