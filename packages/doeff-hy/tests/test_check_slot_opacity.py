"""A mechanical check that doeff_hy._check_slot (M-slot) does not open the policy and holding of the machine check slot canon.

Knowledge guarded: the contents of the canon's policy (field names, values), the width rule for nesting, and the env names belong to the canon, and M-slot does not know them. What M-slot receives from the canon
is only the **unopenable things** — the policy (the answer of read_policy) and the holding (the answer of take) — and an integer, the parallel count (the answer of process_width).

The check before the fix (test_the_canon_policy_goes_straight_into_take) looked at "the answer of read_policy appears only as the first argument of take".
It was narrower and also wider than the knowledge to guard.
- It looked only at the incoming direction: an implementation reading a field from the answer of take (`holding.policy.processes_per_slot`) passed (blind B).
- It refused a correct shape: binding the policy to a name and passing it to the canon's public entry (process_width) made the answer of read_policy appear somewhere other than take, and went red (blind A).

This check decides the uses of the unopenable things by grammar. The unopenable things (the answers of the canon's read_policy and take) may appear in only these 4 positions:
  1. An argument of a call to an entry point the canon declared — `module.take(policy, …)`, `module.process_width(policy, holding, …)`.
  2. Taking out the release entry — `holding.release` (call it, or keep it in SlotHeld. Attributes beyond that are not opened).
  3. A comparison with None — `holding is None` (the mark for when an ancestor holds a slot; the content is not looked at).
  4. Its own binding — `policy = module.read_policy()` (one assignment to a plain name. Copying to another name, or assigning to an attribute or subscript, is a way to open it and is refused).
In addition, the only names this module may read from the canon module are the declared entry points and exceptions (`module.Policy`, `module.PROCESSES_ENV`, etc. are refused).
The canon's own names (declaration keys, env names, old width functions) appear in no source of doeff_hy.
The side that uses the parallel count: the default EXPANDER_LIMIT for machines where no declaration arrives is read only inside expander_limit (the declared count) — if another entry (a new tool that uses the expansion children's pool)
used the default directly as an argument default or a constant, it would start children with a number unrelated to the declaration without taking a slot.

Limits (what cannot be expressed): a new entry that uses the pool without taking a slot (the writer notices because there is no number to pass to expander_limit — this check does not see the forgotten take itself).
Rewriting the rule sets (OPENED, MOUTHS) makes it pass — but the sets are fixed in this file, and changing them is an intended contract change.
The way of opening SlotHeld.let_go on the static_check side (`slot.let_go.__self__`), and the shape of deciding the width on its own without using the answer of process_width, are not seen by this
check (the latter gives the width two owners — recorded in design.md as a design limit).
"""

import ast
import inspect
from collections.abc import Iterator
from pathlib import Path

import pytest
from doeff_hy import _check_slot as check_slot

#: The entry points the canon declared (calls that may be passed unopenable things), the entry points that return unopenable things, and the exceptions that may be caught. Fixed — changing them is a contract change.
MOUTHS = frozenset({"read_policy", "take", "process_width"})
OPENED = frozenset({"read_policy", "take"})
ERRORS = frozenset({"PolicyUnreadable", "SlotWaitExpired"})
#: Names the canon holds and doeff does not (declaration keys, policy fields, env names, old width functions). A copy for the check; add to it following changes in the canon.
CANON_OWNED = frozenset(
    {
        "processesPerSlot",
        "processes_per_slot",
        "waitLimitSeconds",
        "wait_limit_s",
        "AI_CHECK_SLOT_PROCESSES",
        "AI_CHECK_SLOT_HOLDER",
        "PROCESSES_ENV",
        "HOLDER_ENV",
        "width_for_child",
    }
)
SOURCE = inspect.getsource(check_slot)
PACKAGE = Path(inspect.getfile(check_slot)).parent


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _canon_modules(tree: ast.AST) -> frozenset[str]:
    """Names pointing at the canon module — the names receiving the answer of `canon_module(…)`, and arguments annotated with `ModuleType`."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "canon_module"
        ):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        if isinstance(node, ast.arg) and node.annotation is not None and "ModuleType" in ast.unparse(node.annotation):
            names.add(node.arg)
    return frozenset(names)


def _mouth_call(node: ast.AST, modules: frozenset[str], names: frozenset[str]) -> bool:
    """Whether this is a call `module.<one of names>(…)`."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in modules
        and node.func.attr in names
    )


def _is_none(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _argument_of(node: ast.AST, call: ast.AST) -> bool:
    return isinstance(call, ast.Call) and (
        node in call.args or any(node is keyword.value for keyword in call.keywords)
    )


def _release_use_is_closed(release: ast.Attribute, parents: dict[ast.AST, ast.AST]) -> bool:
    """Uses of `holding.release`: calling it, a branch of a None comparison, keeping it in SlotHeld, and assignment to a plain name. Attributes and subscripts beyond that are a way to open it."""
    parent = parents[release]
    if isinstance(parent, ast.Call):
        return parent.func is release or (
            _argument_of(release, parent) and isinstance(parent.func, ast.Name) and parent.func.id == "SlotHeld"
        )
    if isinstance(parent, ast.IfExp):
        return True
    return isinstance(parent, ast.Assign) and all(isinstance(target, ast.Name) for target in parent.targets)


def _name_findings(tree: ast.AST, modules: frozenset[str]) -> Iterator[str]:
    """The only names read from the canon module are the declared entry points and exceptions."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in modules:
            if node.attr not in MOUTHS | ERRORS:
                yield f"{node.lineno}: canon's {node.attr} is neither a declared entry point nor an exception"


def _bound_names(tree: ast.AST, parents: dict[ast.AST, ast.AST], modules: frozenset[str]) -> frozenset[str]:
    """Names receiving the answer of an entry point that returns unopenable things (the left side of one assignment to a plain name)."""
    calls = (node for node in ast.walk(tree) if _mouth_call(node, modules, OPENED))
    assigns = (parents[call] for call in calls)
    return frozenset(
        parent.targets[0].id
        for parent in assigns
        if isinstance(parent, ast.Assign) and len(parent.targets) == 1 and isinstance(parent.targets[0], ast.Name)
    )


def _answer_findings(tree: ast.AST, parents: dict[ast.AST, ast.AST], modules: frozenset[str]) -> Iterator[str]:
    """A call to an entry point that returns unopenable things is either the right side of an assignment to a plain name or an argument of a declared entry point."""
    for node in ast.walk(tree):
        if not _mouth_call(node, modules, OPENED) or not isinstance(node, ast.Call):
            continue
        parent = parents[node]
        named = isinstance(parent, ast.Assign) and len(parent.targets) == 1 and isinstance(parent.targets[0], ast.Name)
        if not (named or (_mouth_call(parent, modules, MOUTHS) and _argument_of(node, parent))):
            yield f"{node.lineno}: canon's {ast.unparse(node.func)} answer is used elsewhere without being bound to a name"


def _compare_with_none(node: ast.Name, parent: ast.AST) -> bool:
    return (
        isinstance(parent, ast.Compare)
        and all(isinstance(op, (ast.Is, ast.IsNot)) for op in parent.ops)
        and all(_is_none(other) for other in (parent.left, *parent.comparators) if other is not node)
    )


def _use_findings(
    tree: ast.AST, parents: dict[ast.AST, ast.AST], modules: frozenset[str], bound: frozenset[str]
) -> Iterator[str]:
    """Uses of a bound name are only the 4 positions in the head note."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Name) and node.id in bound and isinstance(node.ctx, ast.Load)):
            continue
        parent = parents[node]
        if _mouth_call(parent, modules, MOUTHS) and _argument_of(node, parent):
            continue
        if isinstance(parent, ast.Attribute) and parent.value is node and parent.attr == "release":
            if not _release_use_is_closed(parent, parents):
                yield f"{node.lineno}: {node.id}.release is opened beyond that point"
            continue
        if _compare_with_none(node, parent):
            continue
        yield f"{node.lineno}: canon's answer {node.id} is opened inside {type(parent).__name__} ({ast.unparse(parent)})"


def opened_uses(source: str) -> list[str]:
    """Violations in the canon's unopenable things (the answers of read_policy and take) and in the uses of the canon module (with line numbers; empty = closed)."""
    tree = ast.parse(source)
    parents = _parents(tree)
    modules = _canon_modules(tree)
    if not modules:
        return ["no name receiving the canon module (the answer of canon_module) was found — the check sees nothing"]
    bound = _bound_names(tree, parents, modules)
    return [
        *_name_findings(tree, modules),
        *_answer_findings(tree, parents, modules),
        *_use_findings(tree, parents, modules, bound),
    ]


def _identifiers_and_strings(source: str) -> Iterator[tuple[int, str]]:
    """Names in code (identifiers, attributes, arguments, definition names) and string constants that are not docstrings (with lines). Comments and docstrings are not included."""
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            yield node.lineno, node.id
        elif isinstance(node, ast.Attribute):
            yield node.lineno, node.attr
        elif isinstance(node, ast.arg):
            yield node.lineno, node.arg
        elif isinstance(node, ast.alias):
            yield getattr(node, "lineno", 0), node.name.rpartition(".")[2]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield node.lineno, node.name
        elif isinstance(node, ast.keyword) and node.arg is not None:
            yield getattr(node, "lineno", 0), node.arg
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            for owned in CANON_OWNED:
                if owned in node.value:
                    yield node.lineno, owned


def default_limit_uses(source: str) -> list[str]:
    """Where the default number of expansion children EXPANDER_LIMIT is read (the line and the enclosing function name). The definition (an assignment at module level) is not counted."""
    tree = ast.parse(source)
    parents = _parents(tree)

    def enclosing(node: ast.AST) -> str:
        cursor: ast.AST | None = parents.get(node)
        while cursor is not None and not isinstance(cursor, (ast.FunctionDef, ast.AsyncFunctionDef)):
            cursor = parents.get(cursor)
        return "<module>" if cursor is None else cursor.name

    reads = (
        node
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == "EXPANDER_LIMIT" and isinstance(node.ctx, ast.Load))
        or (isinstance(node, ast.Attribute) and node.attr == "EXPANDER_LIMIT")
        or (isinstance(node, ast.alias) and node.name.rpartition(".")[2] == "EXPANDER_LIMIT")
    )
    return [f"{getattr(node, 'lineno', 0)}: {enclosing(node)}" for node in reads]


def canon_owned_names(source: str) -> list[str]:
    """Lines where a name the canon holds appears in code (empty = does not appear)."""
    return [f"{line}: {name}" for line, name in _identifiers_and_strings(source) if name in CANON_OWNED]


# ─────────────────────────────── The real thing

#: Where the default number of expansion children may be read (only the function expander_limit in _static_check.py).
DEFAULT_LIMIT_READERS = {"_static_check.py": ["expander_limit"]}

def test_the_mouths_this_module_declares_are_the_ones_this_guard_allows() -> None:
    """The set of declared entry points is the same as this check's fixed set — adding or changing an entry point is an intended contract change, to be changed together with the set in this file."""
    assert frozenset(check_slot.CANON_CALLS) | frozenset(check_slot.CANON_OPTIONAL_CALLS) == MOUTHS
    assert frozenset(check_slot.CANON_ERRORS) == ERRORS


def test_check_slot_opens_neither_the_canon_policy_nor_what_take_returns() -> None:
    assert opened_uses(SOURCE) == []


def test_the_default_child_count_is_read_only_where_the_slot_width_may_be_missing() -> None:
    """The default EXPANDER_LIMIT for machines where no declaration arrives is read only inside expander_limit (the declared count) — an argument default, a constant of another tool,
    or a direct import would become a second owner that starts expansion children with a number unrelated to the declaration."""
    found = {
        path.name: [hit.partition(": ")[2] for hit in default_limit_uses(path.read_text(encoding="utf-8"))]
        for path in sorted(PACKAGE.rglob("*.py"))
    }
    readers = {name: functions for name, functions in found.items() if functions}
    assert readers == DEFAULT_LIMIT_READERS, readers


def test_no_doeff_hy_source_names_what_the_canon_owns() -> None:
    """Declaration keys, policy fields, env names, and old width functions do not appear in doeff_hy code (excluding docstrings and comments). .hy is looked at as strings per line."""
    in_python = [
        f"{path.name}:{hit}"
        for path in sorted(PACKAGE.rglob("*.py"))
        for hit in canon_owned_names(path.read_text(encoding="utf-8"))
    ]
    in_hy = [
        f"{path.name}:{number}: {name}"
        for path in sorted(PACKAGE.rglob("*.hy"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if not line.lstrip().startswith(";")
        for name in sorted(CANON_OWNED)
        if name in line
    ]
    assert in_python + in_hy == []


# ─────────────────────────────── Checks of the check (good and failing examples) — that the check actually catches and lets correct shapes pass

#: The minimal correct shape (the skeleton of M-slot). Unopenable things are passed as they are to the entry points, only the release entry is kept, and the width receives the answer of process_width.
OPAQUE_ONLY = '''
def take_slot(home, label, out):
    module = canon_module(home, out)
    environ = EnvironmentByName()
    try:
        policy = module.read_policy()
        holding = module.take(policy, label, environ, out)
    except module.PolicyUnreadable as error:
        return SlotHeld()
    except module.SlotWaitExpired as error:
        return SlotExpired(str(error))
    let_go = None if holding is None else holding.release
    width = module.process_width(policy, holding, environ)
    return SlotHeld(let_go, width)
'''

#: Another correct shape (blind A): do not bind the policy; build it inside the argument of the canon's public entry. Both the shape of the check before the fix (the answer of read_policy is the argument of take) and
#: the shape that binds it to a name and also passes it to process_width (the skeleton) pass.
OPAQUE_ONLY_INLINE = OPAQUE_ONLY.replace(
    "policy = module.read_policy()\n        holding = module.take(policy, label, environ, out)",
    "holding = module.take(module.read_policy(), label, environ, out)",
).replace("module.process_width(policy, holding, environ)", "module.process_width(module.read_policy(), holding, environ)")


def test_the_guard_passes_the_opaque_forms() -> None:
    assert "policy = module.read_policy()" in OPAQUE_ONLY
    assert OPAQUE_ONLY_INLINE != OPAQUE_ONLY and "policy = module.read_policy()" not in OPAQUE_ONLY_INLINE
    assert opened_uses(OPAQUE_ONLY) == []
    assert opened_uses(OPAQUE_ONLY_INLINE) == []


def test_the_guard_looks_at_something() -> None:
    """A source with no name receiving the answer of canon_module is refused as 'seeing nothing' (the check does not go green by missing)."""
    assert opened_uses("def f():\n    return 1\n") != []


#: Failing examples: replace one spot of the skeleton with the shape an ordinary implementer would likely write while trying to realize a feature. (name, before replacement, after replacement, the phrase of the violation that must be found)
OPENING_MUTANTS = [
    # Blind B: read the policy field from the answer of take to decide the width.
    (
        "read-the-field-from-the-holding",
        "width = module.process_width(policy, holding, environ)",
        "width = holding.policy.processes_per_slot",
        "answer holding is opened inside Attribute",
    ),
    (
        "read-the-field-from-the-policy",
        "width = module.process_width(policy, holding, environ)",
        "width = policy.processes_per_slot",
        "answer policy is opened inside Attribute",
    ),
    (
        "getattr-the-field",
        "width = module.process_width(policy, holding, environ)",
        "width = getattr(holding, 'policy')",
        "answer holding is opened inside Call",
    ),
    (
        "dump-the-policy",
        "width = module.process_width(policy, holding, environ)",
        "width = vars(policy)",
        "answer policy is opened inside Call",
    ),
    (
        "rebuild-the-policy",
        "holding = module.take(policy, label, environ, out)",
        "holding = module.take(dataclasses.replace(policy, wait_limit_s=1.0), label, environ, out)",
        "answer policy is opened inside Call",
    ),
    (
        "alias-the-policy",
        "holding = module.take(policy, label, environ, out)",
        "spare = policy\n        holding = module.take(spare, label, environ, out)",
        "answer policy is opened inside Assign",
    ),
    (
        "keep-the-holding-on-the-answer",
        "return SlotHeld(let_go, width)",
        "return SlotHeld(holding, width)",
        "answer holding is opened inside Call",
    ),
    (
        "open-through-the-release",
        "let_go = None if holding is None else holding.release",
        "let_go = holding.release.__self__.policy",
        "holding.release is opened beyond that point",
    ),
    (
        "format-the-policy",
        "width = module.process_width(policy, holding, environ)",
        "width = f'{policy}'",
        "answer policy is opened inside FormattedValue",
    ),
    (
        "unpack-into-a-tuple",
        "policy = module.read_policy()",
        "policy, spare = module.read_policy(), 0",
        "used elsewhere without being bound to a name",
    ),
    (
        "use-the-answer-inline-elsewhere",
        "let_go = None if holding is None else holding.release",
        "let_go = module.take(module.read_policy(), label, environ, out).release",
        "used elsewhere without being bound to a name",
    ),
    # Read something other than a declared entry point from the canon module.
    (
        "read-a-canon-constant",
        "width = module.process_width(policy, holding, environ)",
        "width = int(environ.get(module.PROCESSES_ENV, '2'))",
        "canon's PROCESSES_ENV is neither a declared entry point nor an exception",
    ),
    (
        "call-the-old-width-function",
        "width = module.process_width(policy, holding, environ)",
        "width = int(module.width_for_child(policy, holding is None, environ.get('X')))",
        "canon's width_for_child is neither a declared entry point nor an exception",
    ),
]


@pytest.mark.parametrize(("name", "before", "after", "expected"), OPENING_MUTANTS, ids=[m[0] for m in OPENING_MUTANTS])
def test_the_guard_refuses_each_way_of_opening_what_the_canon_gave(
    name: str, before: str, after: str, expected: str
) -> None:
    assert before in OPAQUE_ONLY, f"{name}: the skeleton changed (the line to replace is missing)"
    found = opened_uses(OPAQUE_ONLY.replace(before, after))
    assert any(expected in line for line in found), (name, found)


def test_the_guard_refuses_the_blind_b_patch_as_it_was_written() -> None:
    """The core of blind B's diff: add policy to the holding Protocol, and have SlotHeld read
    processes_per_slot from the holding to decide the width. The holding's field name appears as a code name, so the canon-name check rejects it."""
    patch = '''
class SlotPolicy(Protocol):
    processes_per_slot: int


class Holding(Protocol):
    policy: SlotPolicy

    def release(self) -> None: ...


class SlotHeld:
    def width(self, wanted):
        return max(1, min(wanted, self.holding.policy.processes_per_slot))
'''
    assert canon_owned_names(patch) != []


@pytest.mark.parametrize(
    "written",
    [
        "environ = EnvironmentByName()\nwidth = int(environ['AI_CHECK_SLOT_PROCESSES'])",
        "limit = declaration['processesPerSlot']",
        "from machine_check_slots import width_for_child",
        "slots = policy.processes_per_slot",
    ],
)
def test_the_name_guard_catches_what_the_canon_owns_in_any_code_position(written: str) -> None:
    assert canon_owned_names(written) != [], written


def test_the_name_guard_lets_prose_talk_about_the_canon() -> None:
    prose = '"""The story of the canon\'s processesPerSlot and AI_CHECK_SLOT_PROCESSES (docstring)."""\n# The story of processes_per_slot (comment)\nx = 1\n'
    assert canon_owned_names(prose) == []


@pytest.mark.parametrize(
    ("written", "reader"),
    [
        # An implementation where another tool (the check of type declaration agreement) uses the expansion children's pool — it reads the default number as an argument default.
        (
            "def project_each(root, roots, sources, limit: int = EXPANDER_LIMIT):\n    return _Expanders(limit)\n",
            "project_each",
        ),
        ("from doeff_hy._static_check import EXPANDER_LIMIT\n", "<module>"),
        ("def stale(root):\n    return _Expanders(static_check.EXPANDER_LIMIT)\n", "stale"),
        ("def stale(root):\n    return _Expanders(EXPANDER_LIMIT)\n", "stale"),
    ],
)
def test_the_default_child_count_guard_catches_a_second_reader(written: str, reader: str) -> None:
    assert [hit.partition(": ")[2] for hit in default_limit_uses(written)] == [reader], written


def test_the_default_child_count_guard_accepts_the_one_reader_and_the_definition() -> None:
    allowed = "EXPANDER_LIMIT = 4\n\n\ndef expander_limit(declared):\n    return EXPANDER_LIMIT if declared is None else declared\n"
    assert [hit.partition(": ")[2] for hit in default_limit_uses(allowed)] == ["expander_limit"]
