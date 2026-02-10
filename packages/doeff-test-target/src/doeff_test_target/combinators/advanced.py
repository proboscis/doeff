from doeff import do
from doeff.effects import ask, tell

from ..core.alpha import helper_alpha
from ..core.beta import helper_beta
from ..core.delta import helper_delta
from ..core.gamma import helper_gamma


def helper_eta():
    return eta()


def helper_theta():
    return theta()


def helper_iota():
    return iota()


def helper_kappa():
    return kappa()


@do
def eta():
    previous = yield helper_alpha()
    yield ask("eta")
    return previous


@do
def theta():
    _ = yield helper_beta()
    yield ask("theta")
    yield tell("theta")
    return "theta"


@do
def iota():
    _ = yield helper_gamma()
    yield ask("iota")
    yield tell("iota")
    return "iota"


@do
def kappa():
    _ = yield helper_delta()
    yield ask("kappa")
    return "kappa"
