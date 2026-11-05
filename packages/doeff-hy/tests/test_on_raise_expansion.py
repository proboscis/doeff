"""The on-raise expansion annotates the parameters of each handler function with types (so pyright strict does not report "parameter type is unknown").

The runtime meaning is held by doeff-core-effects' test_outcomes.hy. This test looks only at the text of the expansion: without annotations, each caller that wrote on-raise got
five errors each of "Type of parameter is unknown" and "Type annotation is missing".
"""

from __future__ import annotations

from types import ModuleType

import hy
from hy.models import Expression, Sequence, Symbol

import doeff_hy  # noqa: F401 - register the Hy import hook


def _expand(source: str) -> object:
    module = ModuleType("doeff_hy_on_raise_expansion_probe")
    hy.eval(hy.read("(require doeff-hy.macros [on-raise])"), module=module)
    return hy.macroexpand(hy.read(source), module)


def _annotated_object_params(tree: object) -> list[object]:
    """Collect the parameter lists of `(fn [#^ object name] ...)`."""
    found: list[object] = []
    if isinstance(tree, Expression) and tree and tree[0] == Symbol("fn"):
        found.append(tree[1])
    if isinstance(tree, Sequence):
        for child in tree:
            found.extend(_annotated_object_params(child))
    return found


def test_on_raise_annotates_the_reason_parameter_of_each_case() -> None:
    tree = _expand("(on-raise body (Unreachable detail) detail (Refusal why) why)")
    params = _annotated_object_params(tree)
    assert len(params) == 2, params
    for params_list in params:
        assert "object" in repr(params_list), params_list
        assert "annotate" in repr(params_list).lower(), params_list
