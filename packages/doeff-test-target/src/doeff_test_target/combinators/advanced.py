from doeff import do
from doeff.effects import ask, emit, log

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
    yield emit("theta")
    return "theta"


@do
def iota():
    _ = yield helper_gamma()
    yield ask("iota")
    yield log("iota")
    return "iota"


@do
def kappa():
    _ = yield helper_delta()
    yield ask("kappa")
    return "kappa"
