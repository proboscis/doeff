from doeff import do
from doeff.effects import ask


def helper_epsilon():
    return epsilon()


@do
def epsilon():
    yield ask("epsilon")
    return "epsilon"
