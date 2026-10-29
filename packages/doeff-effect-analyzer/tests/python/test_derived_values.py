"""What the reader derives from a changed module is built by walks that take the shortest way (ki-56e063c79150).

A closure test of the screen job spent 23.5 of 48.1 s on building the derived values (the definition index, the
body nodes, the body facts and the dispatch reading) of the five modules a day's commits had changed, and most of
those seconds were walks: the same body walked for its nodes, for the defs nested in it, for its facts. Three
shortcuts took those walks apart — a body is walked from the fields of each node's class, the facts are read from
the nodes that can bind a name and no others, and the defs nested in a def are the ones among the nodes its body
already lists. Each one gives what the long way gave; these tests compare them with the long way on real sources
(the analyzer's own and the standard library's), where a def has every shape the reader meets."""

import argparse
import ast
import asyncio.tasks
import dataclasses
import inspect
import pickle
import types
import typing
from collections import deque
from collections.abc import Iterator
from pathlib import Path

import pytest
from doeff_effect_analyzer import handler_effects
from doeff_effect_analyzer import _program_effects as pe

FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)

# A source with every shape of a body that binds a name or nests a def: unpacking, loops, imports in a body,
# a class in a def, a lambda in a default, a walrus, a typed local, a def in a branch.
SHAPES = """
import os

def outer(a, b=lambda: 1, *rest, key, **options):
    import json
    from collections import OrderedDict as Ordered
    x = 1
    x = 2
    y: int = 3
    first, second = a
    [left, right] = a
    (p, q) = (1, 2)
    for item in rest:
        z = item
    async def inner():
        async for value in rest:
            pass
        return value
    if (found := a):
        def in_branch():
            return lambda t: t
    class Local:
        attr = 1
        def method(self):
            return [c for c in self.attr]
    return inner, found, in_branch, Local

class Top:
    @staticmethod
    def one(): return lambda: 2
    class Nested:
        def two(self): pass

handlers = [lambda e: e, lambda e, k: (e, k)]

def ordered():
    if os:
        def deep(): pass
    def shallow(): pass
    return lambda: 1
"""


def corpus() -> Iterator[tuple[str, ast.Module]]:
    """The sources the equivalence is checked on: this analyzer's own modules and three of the standard library's."""
    analyzer = Path(str(pe.__file__)).parent
    standard = (ast, inspect, types, typing, dataclasses, argparse, asyncio.tasks)
    files = [*sorted(analyzer.glob("*.py")), *(Path(str(module.__file__)) for module in standard)]
    yield "shapes", ast.parse(SHAPES)
    for file in files:
        yield file.name, ast.parse(file.read_text(encoding="utf-8"), filename=str(file))


def functions_of(tree: ast.Module) -> list[ast.AST]:
    """Every def and lambda of ``tree``, whichever scope it is written in."""
    return [node for node in ast.walk(tree) if isinstance(node, FUNCTIONS)]


def every_node_class() -> frozenset[type]:
    """Every class of node ``ast`` defines — the gate of the facts opened for all of them."""
    return frozenset(
        value for value in vars(ast).values() if isinstance(value, type) and issubclass(value, ast.AST)
    )


def preorder_by_iter_child_nodes(function: ast.AST) -> list[ast.AST]:
    """The nodes of a body the way ``_body_nodes`` was first written: depth-first in source order through
    ``ast.iter_child_nodes``, not entering a def, a class or a lambda."""
    body = [function.body] if isinstance(function, ast.Lambda) else list(function.body)
    stack = list(reversed(body))
    listed = []
    while stack:
        node = stack.pop()
        listed.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(node))))
    return listed


def test_a_body_is_listed_in_the_order_ast_iter_child_nodes_gives() -> None:
    """The walk reads the fields of each node's class itself; the nodes, and their order, are those of the walk
    through ``ast.iter_child_nodes`` — in every def and lambda of the corpus."""
    checked = 0
    for name, tree in corpus():
        for function in functions_of(tree):
            expected = preorder_by_iter_child_nodes(function)
            found = pe._walk_body_nodes(function)
            assert len(found) == len(expected), name
            assert all(a is b for a, b in zip(found, expected, strict=True)), name
            checked += 1
    assert checked > 800  # the corpus has what to check (not a vacuous pass)


def test_the_nodes_the_facts_are_read_from_are_every_node_that_binds() -> None:
    """``_BINDING_NODES`` is the gate in front of the helpers ``_read_body_facts`` asks of each node (a name that is
    read does not pass it either). A node of any other class gives nothing from any of them, in every node of the corpus — so a rule added to a helper for
    another class of node is found here, not by an entry that misses a fact."""
    asked = 0
    for name, tree in corpus():
        for node in ast.walk(tree):
            if type(node) in pe._BINDING_NODES and not (isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Store)):
                continue
            asked += 1
            assert pe._bound_names(node) == set(), (name, node)
            assert pe._import_bindings(node) == {}, (name, node)
            assert pe._assignments(node) == (), (name, node)
            assert pe._unpacked_places(node) == (), (name, node)
            assert not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.For, ast.AsyncFor)), (name, node)
    assert asked > 50_000  # the corpus has what to ask (not a vacuous pass)


def test_a_class_of_node_left_out_of_the_gate_is_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """The negative case of the check above: without ``ast.Assign`` in the gate, nodes of that class give
    something and the check says so."""
    monkeypatch.setattr(pe, "_BINDING_NODES", pe._BINDING_NODES - {ast.Assign})
    with pytest.raises(AssertionError):
        test_the_nodes_the_facts_are_read_from_are_every_node_that_binds()


def test_the_facts_read_from_the_gated_nodes_are_those_read_from_every_node(monkeypatch: pytest.MonkeyPatch) -> None:
    """The facts of every def and lambda of the corpus are the same whether the helpers are asked of the nodes of
    the gate only or of all of them (the facts hold the very nodes of the tree, so they compare by identity)."""
    trees = list(corpus())
    gated = [[pe._read_body_facts(function) for function in functions_of(tree)] for _, tree in trees]
    monkeypatch.setattr(pe, "_BINDING_NODES", every_node_class())
    everything = [[pe._read_body_facts(function) for function in functions_of(tree)] for _, tree in trees]
    assert gated == everything
    assert sum(len(facts) for facts in gated) > 800


def test_the_defs_nested_in_a_def_are_those_the_walk_of_the_chunk_finds() -> None:
    """``_derived`` finds the defs nested in a def among the nodes its body lists, where it walked the chunk again
    for them (``_direct_definitions``). Over the whole corpus, the index it builds is the one built over the whole
    tree (``_definitions_by_path``): the same paths, the same defs in the same order, each at the chunk and the
    place the index says."""
    for name, tree in corpus():
        derived = pe._derived(tree)
        by_path = pe._definitions_by_path(tree)
        assert set(derived.index.index) == set(by_path), name
        for path, entries in derived.index.index.items():
            expected = by_path[path]
            assert len(entries) == len(expected), (name, path)
            for entry, node in zip(entries, expected, strict=True):
                found = derived.definitions[entry.chunk][entry.position]
                assert found is node, (name, path)
                assert entry.is_class == isinstance(node, ast.ClassDef)
                if not entry.is_class:
                    assert entry.lines == pe._definition_lines(node)
                    assert entry.params == pe._definition_params(node)


def test_a_chunk_holds_the_facts_and_the_body_nodes_of_every_def_the_index_names() -> None:
    """What an entry is for: a process that reads the chunk finds the nodes and the facts of each def the index
    names without walking it — every def and lambda of the chunk's definitions has both."""
    for name, tree in corpus():
        derived = pe._derived(tree)
        for number, chunk in enumerate(derived.chunks):
            read = pickle.loads(chunk)
            assert len(read.definitions) == len(derived.definitions[number]), (name, number)
            named = {id(function) for function, _ in read.body_nodes}
            with_facts = {id(function) for function, _ in read.body_facts}
            wanted = {id(node) for node in read.definitions if isinstance(node, FUNCTIONS)}
            assert wanted <= named, (name, number)
            assert wanted <= with_facts, (name, number)


def test_a_miss_reads_the_chunks_it_has_just_built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The process that builds an entry reads the module from what it built: its defs are found from the index
    it has, without a second index over the whole tree (``_definitions_by_path``), and without reading
    the chunks it wrote."""
    monkeypatch.setenv("DOEFF_EFFECT_ANALYZER_CACHE", str(tmp_path / "trees"))
    source = "(require doeff-hy.macros [defk])\n(defn plain [x] (+ x 1))\n(defn other [y] y)\n"
    read = pe._compile_hy(source, "/src/built.hy", "built")
    assert isinstance(read, pe._ChunkedTree)
    assert isinstance(read.tree, ast.Module)

    def refused(*_: object) -> None:
        raise AssertionError("an index over the whole tree was built")

    monkeypatch.setattr(pe, "_definitions_by_path", refused)
    node = next(n for n in ast.walk(read.tree) if isinstance(n, ast.FunctionDef) and n.name == "plain")
    function = types.SimpleNamespace(
        __qualname__="plain",
        __code__=types.SimpleNamespace(co_firstlineno=node.lineno, co_varnames=("x",), co_argcount=1),
    )
    assert read.find(function) is node


def nested_functions_breadth_first(parent: ast.AST) -> list[ast.AST]:
    """The defs and lambdas written directly in ``parent``'s body, the way ``_nested_functions`` found them
    before it took them from the body's node list: breadth-first from the body, stopping at every def, class and
    lambda."""
    queue: deque[ast.AST] = deque([parent.body] if isinstance(parent, ast.Lambda) else parent.body)
    found = []
    while queue:
        node = queue.popleft()
        if isinstance(node, FUNCTIONS):
            found.append(node)
        elif not isinstance(node, ast.ClassDef):
            queue.extend(ast.iter_child_nodes(node))
    return found


def test_the_defs_nested_in_a_def_come_in_the_breadth_first_order() -> None:
    """The nested defs are taken from the body's node list when that gives the breadth-first order, and walked
    breadth-first otherwise; in every def and lambda of the corpus the answer is the breadth-first one — with the
    layouts of ``SHAPES`` in it, a def inside a branch next to one that is a statement among them."""
    several = 0
    for name, tree in corpus():
        for function in functions_of(tree):
            expected = nested_functions_breadth_first(function)
            found = list(handler_effects._nested_functions(function))
            assert len(found) == len(expected), name
            assert all(a is b for a, b in zip(found, expected, strict=True)), name
            several += len(expected) > 1
    assert several >= 5  # defs with more than one nested def are in the corpus (not a vacuous pass)


def effect_param_one_parameter_at_a_time(node: ast.AST) -> str | None:
    """``_effect_param`` as it was first written: each parameter asks the whole body again."""
    positional = [arg.arg for arg in (*node.args.posonlyargs, *node.args.args)]
    if len(positional) < 2:
        return None
    for param in positional:
        tests = (c.test for c in pe._body_nodes(node) if isinstance(c, (ast.If, ast.IfExp)))
        if any(handler_effects._isinstance_target(test, param) is not None for test in tests):
            return param
        if any(isinstance(c, ast.Match) and handler_effects._is_name(c.subject, param) for c in pe._body_nodes(node)):
            return param
    return None


def test_the_parameter_a_def_dispatches_on_is_the_one_each_parameter_asking_finds() -> None:
    """``_effect_param`` lists the body's tests and match subjects once and asks them of each parameter in turn;
    the parameter it names is the one found by asking the whole body for each, in every def of the corpus —
    which has defs that dispatch on a parameter (the analyzer's own handlers)."""
    dispatching = 0
    for name, tree in corpus():
        for function in functions_of(tree):
            found = handler_effects._effect_param(function)
            assert found == effect_param_one_parameter_at_a_time(function), name
            dispatching += found is not None
    assert dispatching > 5
