"""The handler reader finds the functions nested in a dispatcher by reading each body once.

``_clause_functions`` used to ``ast.walk`` the whole subtree at every nesting level and test
each def it met against the parent's body — a node was read once per function enclosing it,
which was a quarter of a closure test's analysis (agora-redesign #1586). ``_nested_functions``
must give the same defs in the same order, since the clauses are read in that order.
"""

import ast
from pathlib import Path

import doeff_effect_analyzer
from doeff_effect_analyzer import handler_effects as he
from doeff_effect_analyzer.program_effects import _body_nodes

FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)

NESTED = """
def outer(effect, k, default=lambda: 0):
    @decorate(lambda: 1)
    def first(effect, k):
        def deep(e, k):
            return lambda: e
        return deep
    class Holder:
        def method(self, effect, k):
            return None
    if isinstance(effect, Ask):
        helper = lambda value: value
        return (lambda: 2)()
    async def second(effect, k):
        return [lambda: 3 for _ in range(2)]
    return first
"""


DISPATCHERS = """
def make(effect, k):
    if isinstance(effect, Ask):
        return None
    def dispatch(effect, k):
        match effect:
            case Ask():
                return None
    return dispatch
"""


def walked(parent: ast.AST) -> list[ast.AST]:
    """The old way: every def in the subtree that is one of the parent's own body nodes."""
    own = _body_nodes(parent)
    return [
        child
        for child in ast.walk(parent)
        if child is not parent
        and isinstance(child, FUNCTIONS)
        and any(node is child for node in own)
    ]


def every_function(tree: ast.AST) -> list[ast.AST]:
    return [node for node in ast.walk(tree) if isinstance(node, FUNCTIONS)]


def test_nested_functions_are_the_walked_ones_in_the_same_order() -> None:
    tree = ast.parse(NESTED)
    for function in every_function(tree):
        assert list(he._nested_functions(function)) == walked(function)
    outer = tree.body[0]
    names = [getattr(node, "name", "<lambda>") for node in he._nested_functions(outer)]
    # Not the default's lambda, the decorator's lambda, the method, or anything deeper.
    assert names == ["first", "second", "<lambda>", "<lambda>"]


def test_nested_functions_match_the_walk_over_the_analyzer_sources() -> None:
    sources = sorted(Path(doeff_effect_analyzer.__file__).parent.glob("*.py"))
    assert sources
    for source in sources:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for function in every_function(tree):
            assert list(he._nested_functions(function)) == walked(function), (source, function.lineno)


def test_clause_functions_are_found_once_per_def(monkeypatch) -> None:
    """A handler is read for every Program installing it; its dispatchers are found once."""
    tree = ast.parse(DISPATCHERS)
    outer = tree.body[0]
    calls: list[ast.AST] = []
    real = he._nested_functions

    def counting(parent: ast.AST):
        calls.append(parent)
        return real(parent)

    monkeypatch.setattr(he, "_nested_functions", counting)
    first = he._clause_functions(outer)
    walked_once = len(calls)
    again = he._clause_functions(outer)
    assert again == first
    assert walked_once > 0
    assert len(calls) == walked_once
    assert [(node.name, [e.name for e in enclosing]) for node, enclosing in first] == [
        ("make", []),
        ("dispatch", ["make"]),
    ]
