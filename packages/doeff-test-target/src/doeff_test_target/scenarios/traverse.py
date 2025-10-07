from doeff import Program, do

from ..core.alpha import helper_alpha
from ..core.beta import helper_beta
from ..core.gamma import helper_gamma


def _builder(name: str):
    if name == "alpha":
        return helper_alpha()
    if name == "beta":
        return helper_beta()
    return helper_gamma()


@do
def traverse_items():
    items = ["alpha", "beta", "gamma"]
    _ = _builder("alpha")
    _ = _builder("beta")
    _ = _builder("gamma")
    results = yield Program.traverse(items, _builder)
    return results
