"""Failure cases for the doeff-hy-check carry (`--carry` -- doeff_hy/_static_carry.py).

The daily type measurement of agora-controllers (hy-type-trend) ran pyright over all ~3,000 .hy files in scope every time, and timed out twice in a row at the 7,200 second limit.
The carry skips pyright for .hy files whose input is unchanged since the previous run and carries over the previous run's diagnostics. If the carried answer
differs from the full-run answer, type errors get missed (fixed errors linger, new errors do not appear), so it is pinned here:

- Surface: the body and docstring of a fully type-declared function do not enter the consumer's input. Signatures, bodies of functions without type declarations, and bodies of methods
    that assign to self attributes do enter it.
- Dependencies: depends only on the declarations of the names used (changes to other names in the same module are not propagated). Declarations reached transitively, changes where a new file resolves an import, py.typed,
    star imports, and unreadable modules (always re-checked) are propagated.
- Carry: on the second run only files whose input changed are passed to pyright, and the carried diagnostics go into the output. If the carry file is unreadable, everything is checked.
- With real pyright, the carried run's answer equals the answer of a run that checks the same tree in full (body-only change, signature change of a used name, transitive type change,
    a change that makes a previously unresolved import resolve).
"""

import contextlib
import importlib.util
import io
import json
import shlex
import shutil
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import doeff_hy  # noqa: F401  # enable the Hy import hook
import pytest
from doeff_hy import CarryUnreadableError
from doeff_hy import _static_carry as static_carry
from doeff_hy import _static_check as static_check
from doeff_hy._check_slot import SlotHeld
from doeff_hy._static_carry import (
    CarriedFile,
    CarriedRow,
    CarryRecord,
    Environment,
    FileInputs,
    FileRows,
    InputGraph,
    module_facts,
    next_record,
    places_of,
    plan_carry,
    read_carry,
)

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright not found")


# --- Surface ------------------------------------------------------------------------------------------------------------------


def _surfaces(text: str) -> dict[str, tuple[str, ...]]:
    """Per-name surfaces of a module (the sequence of ast.dump of the statements binding each name)."""
    facts = module_facts(text)
    return {
        name: tuple(facts.statements[i].surface for i in indexes)
        for name, indexes in facts.bound.items()
    }


def test_the_body_and_docstring_of_a_fully_annotated_function_are_not_its_surface() -> None:
    """A fully type-declared function has the same surface even when its body and docstring change -- consumers are not re-checked."""
    before = _surfaces('def f(x: int) -> str:\n    "description"\n    return str(x)\n')
    after = _surfaces('def f(x: int) -> str:\n    "another description"\n    y = x + 1\n    return str(y)\n')
    assert before["f"] == after["f"]


def test_the_signature_of_a_function_is_its_surface() -> None:
    """Changing the argument or return type changes the surface."""
    assert (
        _surfaces("def f(x: int) -> str:\n    return ''\n")["f"]
        != _surfaces("def f(x: int) -> int:\n    return 0\n")["f"]
    )
    assert (
        _surfaces("def f(x: int) -> str:\n    return ''\n")["f"]
        != _surfaces("def f(x: str) -> str:\n    return ''\n")["f"]
    )


def test_the_body_of_a_function_without_its_return_type_is_its_surface() -> None:
    """A function without a declared return type has its body as part of the surface too, because pyright infers the return type from the body."""
    assert (
        _surfaces("def f(x: int):\n    return 1\n")["f"]
        != _surfaces("def f(x: int):\n    return 'a'\n")["f"]
    )


def test_an_empty_body_and_a_full_body_have_different_surfaces() -> None:
    """An empty body (counted as a Protocol method with no implementation) and a body with content are distinguished by the surface."""
    assert (
        _surfaces("def f(x: int) -> int:\n    ...\n")["f"]
        != _surfaces("def f(x: int) -> int:\n    return x\n")["f"]
    )


def test_a_method_that_assigns_to_self_keeps_its_body_in_the_class_surface() -> None:
    """The body of a method that assigns to a self attribute is part of the class surface (pyright infers the attribute type from it). The body of a method that does not assign is not."""
    assigning = "class C:\n    def __init__(self, x: int) -> None:\n        self.x = {}\n"
    assert _surfaces(assigning.format("x"))["C"] != _surfaces(assigning.format("str(x)"))["C"]
    reading = "class C:\n    def size(self, x: int) -> int:\n        return {}\n"
    assert _surfaces(reading.format("x"))["C"] == _surfaces(reading.format("x + 1"))["C"]


def test_each_name_of_a_module_import_statement_is_its_own_surface() -> None:
    """Adding a name to one import statement does not change the surfaces of the other names (the long import statement in doeff/__init__)."""
    before = _surfaces("from m import a, b\n")
    after = _surfaces("from m import a, b, c\n")
    assert before["a"] == after["a"]
    assert before["b"] == after["b"]


# --- Plan and carry document ------------------------------------------------------------------------------------------------------


def _carried(path: str, inputs: str, checked: int, *messages: str) -> CarriedFile:
    """One file of the carry document (the diagnostic is a row with only the message text changed)."""
    return CarriedFile(
        path, inputs, checked, tuple(CarriedRow(1, 1, "error", "rule", m) for m in messages)
    )


def test_without_a_previous_carry_every_file_is_checked() -> None:
    """A run with no carry, an unreadable carry, or a different environment checks everything."""
    inputs = (FileInputs("a.hy", "1"), FileInputs("b.hy", "2"))
    assert {c.reason for c in plan_carry(None, "f", inputs, 12).checking} == {"absent"}
    assert {c.reason for c in plan_carry(CarryUnreadableError("x"), "f", inputs, 12).checking} == {
        "unreadable"
    }
    previous = CarryRecord("old", 3, (_carried("a.hy", "1", 3), _carried("b.hy", "2", 3)))
    plan = plan_carry(previous, "new", inputs, 12)
    assert {c.reason for c in plan.checking} == {"environment"}
    assert plan.run == 4


def test_only_changed_and_new_files_are_checked_and_the_oldest_are_refreshed() -> None:
    """Check files whose input changed and files absent from the previous run, and of the unchanged files re-review the ceil(count / period) files whose checked run is oldest."""
    previous = CarryRecord(
        "f",
        9,
        (
            _carried("a.hy", "1", 9),
            _carried("b.hy", "2", 4),
            _carried("c.hy", "3", 7),
            _carried("d.hy", "4", 9),
        ),
    )
    inputs = (
        FileInputs("a.hy", "1"),
        FileInputs("b.hy", "2"),
        FileInputs("c.hy", "x"),
        FileInputs("d.hy", "4"),
        FileInputs("e.hy", "5"),
    )
    plan = plan_carry(previous, "f", inputs, 5)
    assert {c.path: c.reason for c in plan.checking} == {
        "c.hy": "changed",
        "e.hy": "new",
        "b.hy": "refresh",
    }
    assert sorted(c.path for c in plan.carried) == ["a.hy", "d.hy"]
    assert plan.run == 10
    assert {c.path for c in plan_carry(previous, "f", inputs, 0).checking} == {
        "c.hy",
        "e.hy",
    }  # 0 = no re-review


def test_the_next_record_keeps_carried_rows_and_names_refresh_mismatches() -> None:
    """Checked files carry this run's diagnostics and run number; carried files keep the previous run's record. A differing answer on re-review is reported."""
    previous = CarryRecord(
        "f",
        2,
        (
            _carried("a.hy", "1", 2, "a1"),
            _carried("b.hy", "2", 1, "b1"),
            _carried("c.hy", "3", 2, "c1"),
        ),
    )
    inputs = (FileInputs("a.hy", "1"), FileInputs("b.hy", "2"), FileInputs("c.hy", "9"))
    plan = plan_carry(previous, "f", inputs, 3)
    fresh = (
        FileRows("b.hy", (CarriedRow(1, 1, "error", "rule", "b2"),)),
        FileRows(
            "c.hy",
            (CarriedRow(2, 1, "error", "rule", "c2"), CarriedRow(1, 1, "error", "rule", "c3")),
        ),
    )
    record, summary = next_record(plan, "f", inputs, fresh)
    by_path = {f.path: f for f in record.files}
    assert by_path["a.hy"] == previous.files[0]
    assert by_path["b.hy"].checked == 3
    assert [r.message for r in by_path["c.hy"].rows] == ["c2", "c3"]  # in pyright's answer order
    assert summary.mismatched == ("b.hy",)
    assert (summary.checked, summary.carried) == (2, 1)
    text = static_carry.carry_text(record, summary)
    assert json.loads(text)["summary"]["refresh_mismatches"] == 1


def test_a_partial_record_keeps_only_finished_and_still_valid_files() -> None:
    """The intermediate document for each group holds this run's diagnostics for files already checked, carried files, and the previous record for re-review files not yet checked, and
        holds no changed or new files not yet checked (the next run checks them as new). The count covers only files already checked."""
    previous = CarryRecord(
        "f",
        2,
        (
            _carried("a.hy", "1", 2, "a1"),
            _carried("b.hy", "2", 1, "b1"),
            _carried("c.hy", "3", 2, "c1"),
        ),
    )
    inputs = (
        FileInputs("a.hy", "1"),
        FileInputs("b.hy", "2"),
        FileInputs("c.hy", "9"),
        FileInputs("d.hy", "4"),
    )
    plan = plan_carry(previous, "f", inputs, 4)  # re-review is ceil(4 / 4) = 1 file -- b, whose checked run is oldest
    assert {c.path: c.reason for c in plan.checking} == {
        "c.hy": "changed",
        "d.hy": "new",
        "b.hy": "refresh",
    }
    fresh = (FileRows("c.hy", (CarriedRow(1, 1, "error", "rule", "c2"),)),)
    record, summary = next_record(plan, "f", inputs, fresh, frozenset({"c.hy"}))
    by_path = {f.path: f for f in record.files}
    assert sorted(by_path) == ["a.hy", "b.hy", "c.hy"]
    assert by_path["a.hy"] == previous.files[0]
    assert by_path["b.hy"] == previous.files[1]  # not yet re-reviewed -- previous record kept
    assert ([r.message for r in by_path["c.hy"].rows], by_path["c.hy"].checked) == (["c2"], 3)
    assert (summary.checked, summary.carried, summary.mismatched) == (1, 1, ())
    # The next run checks d, which had not been checked, as new, and carries c, which was already checked.
    after = plan_carry(record, "f", inputs, 0)
    assert {c.path: c.reason for c in after.checking} == {"d.hy": "new"}


def test_the_carry_file_round_trips_and_a_broken_one_is_unreadable(tmp_path: Path) -> None:
    """A written carry file reads back as the same record. A broken file or a file of a different version raises CarryUnreadableError (not silently read as empty)."""
    record = CarryRecord("f", 5, (_carried("a.hy", "1", 5, "m"),))
    plan = plan_carry(None, "f", (FileInputs("a.hy", "1"),), 12)
    _, summary = next_record(plan, "f", (FileInputs("a.hy", "1"),), ())
    path = tmp_path / "carry.json"
    static_carry.write_carry(path, static_carry.carry_text(record, summary))
    assert read_carry(path) == record
    assert read_carry(tmp_path / "missing.json") is None
    for broken in (
        "{",
        '{"version": 2}',
        '{"version": 1, "fingerprint": "f", "run": 1, "files": {"a": {}}}',
    ):
        path.write_text(broken, encoding="utf-8")
        with pytest.raises(CarryUnreadableError):
            read_carry(path)


# --- Environment fingerprint ------------------------------------------------------------------------------------------------------------


def _function_from(place: Path, source: str) -> Callable[..., object]:
    """Write source to a module file, read it, and return the function f in it (a function whose source inspect can read)."""
    place.mkdir(parents=True)
    path = place / "semantics_case.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(f"semantics_case_{place.name}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return vars(module)["f"]


def test_a_comment_only_change_keeps_the_semantics_digest(tmp_path: Path) -> None:
    """Rewriting only comments or blank lines of the function that produces diagnostics does not change the fingerprint (does not re-check all .hy), while a code change does."""
    plain = _function_from(tmp_path / "a", "def f(x: int) -> int:\n    return x + 1\n")
    commented = _function_from(
        tmp_path / "b", "def f(x: int) -> int:  # noqa: DOEFF007 - note\n\n    return x + 1  # note\n"
    )
    changed = _function_from(tmp_path / "c", "def f(x: int) -> int:\n    return x + 2\n")
    assert static_carry.semantics_digest([plain]) == static_carry.semantics_digest([commented])
    assert static_carry.semantics_digest([plain]) != static_carry.semantics_digest([changed])


def test_an_editable_distribution_version_is_not_in_the_listing(tmp_path: Path) -> None:
    """Bumping the version of an editable-installed distribution (the doeff checkout) does not change the material of the search-path fingerprint, while bumping the version of a non-editable distribution does."""

    def listing(editable_version: str, other_version: str) -> list[str]:
        place = tmp_path / f"{editable_version}-{other_version}"
        editable = place / f"doeff_hy-{editable_version}.dist-info"
        editable.mkdir(parents=True, exist_ok=True)
        (editable / "direct_url.json").write_text(
            '{"url": "file:///x", "dir_info": {"editable": true}}', encoding="utf-8"
        )
        (place / f"httpx-{other_version}.dist-info").mkdir(exist_ok=True)
        return static_carry._listing(place)

    assert listing("0.5.0", "0.28.1") == listing("0.6.0", "0.28.1")
    assert listing("0.5.0", "0.28.1") != listing("0.5.0", "0.29.0")


# --- Dependency digest ---------------------------------------------------------------------------------------------------------


class _Tree:
    """A small tree for measuring the input digest -- target.hy under the root (with Python standing in for expansion passed as staged) and .py modules under the root."""

    def __init__(self, root: Path, target: str) -> None:
        self.root = root
        self.target = target

    def write(self, relative: str, text: str) -> None:
        """Write a file under the root (creating dirs too)."""
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def digest(self, searched: tuple[Path, ...] = ()) -> str:
        """The input digest of target.hy (a fresh graph each time -- same as one run). searched = a python search path that looks at contents file by file."""
        source = self.root / "target.hy"
        environment = Environment("fingerprint", searched, places_of(self.root))
        graph = InputGraph(
            self.root, [self.root], self.root / "typings", environment, {source: self.target}, ()
        )
        return graph.inputs_of(source, "own")


@pytest.fixture
def tree(tmp_path: Path) -> Iterator[_Tree]:
    root = tmp_path / "repo"
    root.mkdir()
    return _Tree(root, "from pkg.impl import used\n\ndef go() -> int:\n    return used(1)\n")


def test_only_the_declarations_a_file_uses_are_its_inputs(tree: _Tree) -> None:
    """Changing the signature of a used name changes the input; changing the body of a used name or the signature of an unused name in the same module does not."""
    tree.write("pkg/__init__.py", "")
    tree.write(
        "pkg/impl.py",
        "def used(x: int) -> int:\n    return x\n\ndef unused(x: int) -> int:\n    return x\n",
    )
    first = tree.digest()
    tree.write(
        "pkg/impl.py",
        "def used(x: int) -> int:\n    return x + 1\n\ndef unused(x: str) -> str:\n    return x\n",
    )
    assert tree.digest() == first
    tree.write(
        "pkg/impl.py",
        "def used(x: int) -> str:\n    return ''\n\ndef unused(x: str) -> str:\n    return x\n",
    )
    assert tree.digest() != first


def test_a_declaration_reached_through_a_signature_is_an_input(tree: _Tree) -> None:
    """Changing a type read by the signature of a used name (a class in another module) changes the input -- a declaration reached transitively."""
    tree.write("pkg/__init__.py", "")
    tree.write("pkg/kinds.py", "class Size:\n    n: int\n")
    tree.write(
        "pkg/impl.py",
        "from pkg.kinds import Size\n\ndef used(x: int) -> 'Size':\n    return Size()\n",
    )
    first = tree.digest()
    tree.write("pkg/kinds.py", "class Size:\n    n: str\n")
    assert tree.digest() != first


def test_a_cycle_of_declarations_still_carries_a_change_across_it(tree: _Tree) -> None:
    """Even if declarations form a cycle (the signature of a.used reads b.Size and a method of b.Size calls a.used), changes to declarations in the cycle propagate to consumers, and
        a body-only change of a function in the cycle does not."""
    tree.write("pkg/__init__.py", "")
    tree.write(
        "pkg/impl.py", "from pkg.kinds import Size\n\ndef used(x: 'Size') -> int:\n    return 1\n"
    )
    tree.write(
        "pkg/kinds.py",
        "from pkg.impl import used\n\nclass Size:\n    n: int\n\n    def twice(self) -> int:\n        return used(self) * 2\n",
    )
    first = tree.digest()
    tree.write(
        "pkg/impl.py", "from pkg.kinds import Size\n\ndef used(x: 'Size') -> int:\n    return 2\n"
    )
    assert tree.digest() == first
    tree.write(
        "pkg/kinds.py",
        "from pkg.impl import used\n\nclass Size:\n    n: str\n\n    def twice(self) -> int:\n        return used(self) * 2\n",
    )
    assert tree.digest() != first


def test_a_re_export_through_a_package_init_reaches_the_declaration(tree: _Tree) -> None:
    """Even when a package __init__ re-exports a name (from .impl import used), it depends on the declaration at the re-exported target. Other names in __init__ are not propagated."""
    tree.target = "from pkg import used\n\ndef go() -> int:\n    return used(1)\n"
    tree.write("pkg/__init__.py", "from .impl import used\nfrom .impl import other\n")
    tree.write(
        "pkg/impl.py",
        "def used(x: int) -> int:\n    return x\n\ndef other() -> int:\n    return 1\n",
    )
    first = tree.digest()
    tree.write(
        "pkg/impl.py",
        "def used(x: int) -> int:\n    return x\n\ndef other() -> str:\n    return ''\n",
    )
    assert tree.digest() == first
    tree.write(
        "pkg/impl.py",
        "def used(x: str) -> int:\n    return 0\n\ndef other() -> str:\n    return ''\n",
    )
    assert tree.digest() != first


def test_a_new_file_that_resolves_an_import_changes_the_inputs(tree: _Tree) -> None:
    """Adding a file that resolves a previously unresolved import changes the input (the previous run's "unresolved" error is not carried)."""
    tree.write("pkg/__init__.py", "")
    first = tree.digest()
    tree.write("pkg/impl.py", "def used(x: int) -> int:\n    return x\n")
    assert tree.digest() != first


def test_a_py_typed_marker_changes_the_inputs(tree: _Tree) -> None:
    """Adding py.typed to a package (which changes how pyright reads it) changes the input."""
    tree.write("pkg/__init__.py", "")
    tree.write("pkg/impl.py", "def used(x: int) -> int:\n    return x\n")
    first = tree.digest()
    tree.write("pkg/py.typed", "")
    assert tree.digest() != first


def test_a_star_import_depends_on_the_whole_module(tree: _Tree) -> None:
    """A star import depends on all names of the star module."""
    tree.target = "from pkg.impl import *\n"
    tree.write("pkg/__init__.py", "")
    tree.write("pkg/impl.py", "def used(x: int) -> int:\n    return x\n")
    first = tree.digest()
    tree.write(
        "pkg/impl.py",
        "def used(x: int) -> int:\n    return x\n\ndef more() -> int:\n    return 1\n",
    )
    assert tree.digest() != first


def test_a_module_used_as_an_attribute_chain_depends_on_the_attribute_only(tree: _Tree) -> None:
    """m.used from `import pkg.impl as m` depends only on the declaration of used."""
    tree.target = "import pkg.impl as m\n\ndef go() -> int:\n    return m.used(1)\n"
    tree.write("pkg/__init__.py", "")
    tree.write(
        "pkg/impl.py",
        "def used(x: int) -> int:\n    return x\n\ndef unused() -> int:\n    return 1\n",
    )
    first = tree.digest()
    tree.write(
        "pkg/impl.py",
        "def used(x: int) -> int:\n    return x\n\ndef unused() -> str:\n    return ''\n",
    )
    assert tree.digest() == first
    tree.write(
        "pkg/impl.py",
        "def used(x: int) -> str:\n    return ''\n\ndef unused() -> str:\n    return ''\n",
    )
    assert tree.digest() != first


def test_another_name_in_all_does_not_change_the_inputs_but_the_used_name_does(tree: _Tree) -> None:
    """Adding another name to __all__ does not change the input of a used name, while removing the used name from __all__ does (only presence is checked per name)."""
    body = "\n\ndef used(x: int) -> int:\n    return x\n\ndef more() -> int:\n    return 1\n"
    tree.write("pkg/__init__.py", "")
    tree.write("pkg/impl.py", "__all__ = ['used']" + body)
    first = tree.digest()
    tree.write("pkg/impl.py", "__all__ = ['used']\n__all__ += ['more']" + body)
    assert tree.digest() == first
    tree.write("pkg/impl.py", "__all__ = ['more']" + body)
    assert tree.digest() != first


def test_an_all_pyright_cannot_read_changes_the_inputs_of_every_name(tree: _Tree) -> None:
    """A module that writes __all__ in a form pyright cannot read (built by an expression) propagates changes to the __all__ statement to consumers of all names."""
    body = "\n\ndef used(x: int) -> int:\n    return x\n\ndef more() -> int:\n    return 1\n"
    tree.write("pkg/__init__.py", "")
    tree.write("pkg/impl.py", "__all__ = ['used'] + []" + body)
    first = tree.digest()
    tree.write("pkg/impl.py", "__all__ = ['used'] + ['more']" + body)
    assert tree.digest() != first


def test_the_same_tree_in_another_place_has_the_same_inputs(tmp_path: Path) -> None:
    """The daily run places the root under a per-task work dir and places the doeff pin checkout next to the root (the path differs every run) -- measuring the same tree and neighbor at different
        locations gives the same input (if it differed, everything would be checked every time)."""
    digests = []
    for run in ("task_t1", "task_t2"):
        parent = tmp_path / run / "worktrees"
        tree = _Tree(
            parent / f"repo-{run}",
            "from pkg.impl import used\nfrom shared import helper\n\ndef go() -> int:\n    return used(helper())\n",
        )
        tree.write("pkg/__init__.py", "")
        tree.write("pkg/impl.py", "def used(x: int) -> int:\n    return x\n")
        (parent / "lib").mkdir()
        (parent / "lib" / "shared.py").write_text(
            "def helper() -> int:\n    return 1\n", encoding="utf-8"
        )
        digests.append(tree.digest(searched=(parent / "lib",)))
    assert digests[0] == digests[1]


def test_places_spell_the_root_and_its_parent_but_not_a_longer_name(tmp_path: Path) -> None:
    """The location marker applies only where the spelling is followed by a path continuation or separator (another dir whose name starts with the root name is a dir under the root's parent)."""
    root = tmp_path / "worktrees" / "repo-a"
    text = json.dumps(
        [
            str(root / "x.py"),
            str(root.parent / "doeff" / "src"),
            str(root),
            f"{root}2/y.py",
            "/elsewhere/z",
        ]
    )
    assert json.loads(places_of(root).spell(text)) == [
        "@root/x.py",
        "@parent/doeff/src",
        "@root",
        "@parent/repo-a2/y.py",
        "/elsewhere/z",
    ]


def test_an_unreadable_module_makes_every_user_checked_every_run(tree: _Tree) -> None:
    """A file that uses a module unreadable due to a syntax error gets a different digest every run (checked every time -- the used names cannot be split out)."""
    tree.write("pkg/__init__.py", "")
    tree.write("pkg/impl.py", "def used(x: int) -> int\n")
    assert tree.digest() != tree.digest()


# --- Entry point (fake pyright)-------------------------------------------------------------------------------------------------


def _fake_pyright(tmp_path: Path, log: Path) -> Path:
    """A fake pyright that answers the version for `--version` and returns one "seen <name>" error per checked file (writing the names of received files to a log)."""
    script = tmp_path / "fake-pyright.py"
    script.write_text(
        "import json, sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('pyright 1.1.414')\n"
        "    raise SystemExit(0)\n"
        "targets = [a for a in sys.argv[1:] if a.endswith('.py')]\n"
        f"with open({str(log)!r}, 'a', encoding='utf-8') as out:\n"
        "    out.write(' '.join(sorted(t.rsplit('/', 1)[-1] for t in targets)) + '\\n')\n"
        "diagnostics = [{'file': t, 'severity': 'error', 'rule': 'fake', 'message': 'seen ' + t.rsplit('/', 1)[-1],\n"
        "                'range': {'start': {'line': 0, 'character': 0}}} for t in targets]\n"
        "print(json.dumps({'generalDiagnostics': diagnostics}))\n",
        encoding="utf-8",
    )
    pyright = tmp_path / "fake-pyright"
    pyright.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(script))} "$@"\n',
        encoding="utf-8",
    )
    pyright.chmod(0o755)
    return pyright


def _module(body: str) -> str:
    """A defk .hy module (requires defk and <-)."""
    return f"(require doeff-hy.macros [defk <-])\n{body}\n"


def _run(root: Path, *extra: str) -> tuple[int, list[dict[str, object]], str]:  # noqa: DOEFF006 — return of a check helper
    """Run the entry point main once and return the exit code, the JSON diagnostics, and stderr."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = static_check.main(
            ["--root", str(root), "--json", "--no-cache", *extra, str(root / "pkg")]
        )
    text = out.getvalue()
    return code, (json.loads(text) if text.strip() else []), err.getvalue()


@pytest.fixture
def entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An entry root that does not take the machine check slot (3 .hy files under pkg -- user uses the width of base, alone is used by nobody)."""
    monkeypatch.setattr(sys, "path", list(sys.path))  # main prepends the root to sys.path
    monkeypatch.setattr(static_check, "take_slot", lambda *_: SlotHeld())
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.hy").write_text("", encoding="utf-8")
    (root / "pkg" / "base.hy").write_text(
        _module(
            '(defk width [text]\n  {:pre [(: text str)] :post [(: % int)]}\n  "width"\n  (len text))'
        ),
        encoding="utf-8",
    )
    (root / "pkg" / "user.hy").write_text(
        _module(
            "(import pkg.base [width])\n(defk shout [text]\n  {:pre [(: text str)] :post [(: % int)]}\n  (<- n (width text))\n  n)"
        ),
        encoding="utf-8",
    )
    (root / "pkg" / "alone.hy").write_text(
        _module("(defk twice [n]\n  {:pre [(: n int)] :post [(: % int)]}\n  (* n 2))"),
        encoding="utf-8",
    )
    return root


def _pyright_errors(diagnostics: list[dict[str, object]]) -> list[tuple[str, str]]:
    """Only the fake errors (rule fake), as (path, message)."""
    return sorted((str(d["path"]), str(d["message"])) for d in diagnostics if d["rule"] == "fake")


def test_the_second_run_checks_only_changed_files_and_carries_the_rest(
    entry: Path, tmp_path: Path
) -> None:
    """The second run passes only files whose input changed to pyright, and the diagnostics of carried files enter the output as in the previous run (the same shape as the full-run answer)."""
    log = tmp_path / "pyright.log"
    pyright = _fake_pyright(tmp_path, log)
    carry = tmp_path / "carry.json"
    common = ("--pyright", str(pyright), "--carry", str(carry), "--carry-refresh-runs", "0")
    code, first, err = _run(entry, *common)
    assert code == 1, err
    assert log.read_text(encoding="utf-8").splitlines() == ["__init__.py alone.py base.py user.py"]
    assert "checked with pyright: 4 .hy files, carried from previous run: 0 .hy files" in err
    # Change only the body (the type of width is the same) -- check only base, carry user and alone.
    base = entry / "pkg" / "base.hy"
    base.write_text(
        base.read_text(encoding="utf-8").replace("(len text))", "(len (.strip text)))"),
        encoding="utf-8",
    )
    code, second, err = _run(entry, *common)
    assert log.read_text(encoding="utf-8").splitlines()[-1] == "base.py", err
    assert "checked with pyright: 1 .hy files, carried from previous run: 3 .hy files" in err
    assert _pyright_errors(second) == _pyright_errors(first)
    # Change the signature of a used name -- check base and user.
    base.write_text(
        base.read_text(encoding="utf-8").replace(":post [(: % int)]", ":post [(: % str)]"),
        encoding="utf-8",
    )
    _run(entry, *common)
    assert log.read_text(encoding="utf-8").splitlines()[-1] == "base.py user.py"
    summary = json.loads(carry.read_text(encoding="utf-8"))["summary"]
    assert (summary["checked"], summary["carried"], summary["reasons"]["changed"]) == (2, 2, 2)


def test_groups_check_the_same_files_and_a_cut_run_keeps_the_finished_groups(
    entry: Path, tmp_path: Path
) -> None:
    """--carry-group splits the files to check into groups for pyright, and the answer is the same as without splitting. Even if pyright dies on the second group, the carry file
        holds the first group's diagnostics, and the next run checks only files not yet checked."""
    log = tmp_path / "pyright.log"
    pyright = _fake_pyright(tmp_path, log)
    carry = tmp_path / "carry.json"
    common = ("--pyright", str(pyright), "--carry", str(carry), "--carry-refresh-runs", "0")
    _, whole, _ = _run(entry, *common)
    carry.unlink()
    code, grouped, err = _run(entry, *common, "--carry-group", "2")
    assert code == 1, err
    assert log.read_text(encoding="utf-8").splitlines()[-2:] == ["__init__.py alone.py", "base.py user.py"]
    assert _pyright_errors(grouped) == _pyright_errors(whole)
    assert "checked with pyright: 4 .hy files" in err
    # pyright dies on the second group -- only the 2 files of the first group remain in the carry file.
    carry.unlink()
    broken = tmp_path / "broken-pyright"
    broken.write_text(
        "#!/bin/sh\n"
        f"if [ -e {shlex.quote(str(tmp_path / 'once'))} ] && [ \"$1\" != --version ]; then echo broken; exit 3; fi\n"
        f"[ \"$1\" != --version ] && touch {shlex.quote(str(tmp_path / 'once'))}\n"
        f'exec {shlex.quote(str(pyright))} "$@"\n',
        encoding="utf-8",
    )
    broken.chmod(0o755)
    code, _, err = _run(
        entry, "--pyright", str(broken), "--carry", str(carry), "--carry-refresh-runs", "0", "--carry-group", "2"
    )
    assert code == 2
    assert "cannot read pyright output" in err
    kept = read_carry(carry)
    assert kept is not None
    assert sorted(f.path for f in kept.files) == ["pkg/__init__.hy", "pkg/alone.hy"]
    _run(entry, *common, "--carry-group", "2")
    assert log.read_text(encoding="utf-8").splitlines()[-1] == "base.py user.py"


def test_an_unreadable_carry_file_checks_everything_and_is_replaced(
    entry: Path, tmp_path: Path
) -> None:
    """If the carry file is unreadable, report it and check everything (do not stop the measurement), replacing it with this run's carry."""
    log = tmp_path / "pyright.log"
    pyright = _fake_pyright(tmp_path, log)
    carry = tmp_path / "carry.json"
    carry.write_text("{ broken", encoding="utf-8")
    code, diagnostics, err = _run(entry, "--pyright", str(pyright), "--carry", str(carry))
    assert code == 1
    assert "is not JSON" in err
    assert len(_pyright_errors(diagnostics)) == 4
    assert read_carry(carry) is not None


# --- Real pyright --------------------------------------------------------------------------------------------------------


def _real(root: Path, *extra: str) -> list[dict[str, object]]:
    """JSON diagnostics (position, rule, message) from running real pyright in strict mode."""
    code, diagnostics, err = _run(root, "--strict", *extra)
    assert code in (0, 1), err
    return diagnostics


def _same_as_full(root: Path, carry: Path) -> tuple[int, int]:  # noqa: DOEFF006 — return of a check helper
    """Verify that the answer under the carry equals the answer of a full run over the same tree, and return the number checked by pyright and the number carried in that run (checking everything without carrying
        gives the same answer, so the counts confirm that carrying happened)."""
    carried = _real(root, "--carry", str(carry), "--carry-refresh-runs", "0")
    assert carried == _real(root)
    summary = json.loads(carry.read_text(encoding="utf-8"))["summary"]
    return summary["checked"], summary["carried"]


@needs_pyright
def test_carried_answers_match_a_full_check_across_changes(entry: Path, tmp_path: Path) -> None:
    """With real pyright: no change, changing only the body of a used name, changing the return type of a used name so an error appears in the consumer, and removing that error -- in every run, the carried answer
        equals the answer of a full run over the same tree."""
    carry = tmp_path / "carry.json"
    assert _same_as_full(entry, carry) == (4, 0)  # first run (checks everything -- __init__, alone, base, user)
    assert _same_as_full(entry, carry) == (0, 4)  # no change (carries everything)
    base = entry / "pkg" / "base.hy"
    base.write_text(
        base.read_text(encoding="utf-8").replace("(len text))", "(len (.strip text)))"),
        encoding="utf-8",
    )
    assert _same_as_full(entry, carry) == (1, 3)  # base only
    base.write_text(
        base.read_text(encoding="utf-8")
        .replace(":post [(: % int)]", ":post [(: % str)]")
        .replace("(len (.strip text)))", "(.strip text))"),
        encoding="utf-8",
    )
    user_errors = [
        d for d in _real(entry) if d["path"] == "pkg/user.hy" and d["severity"] == "error"
    ]
    assert user_errors, "changing the return type to str should produce an error in user's shout (which returns int)"
    assert _same_as_full(entry, carry) == (2, 2)  # base, and user which uses width
    base.write_text(
        base.read_text(encoding="utf-8")
        .replace(":post [(: % str)]", ":post [(: % int)]")
        .replace("(.strip text))", "(len text))"),
        encoding="utf-8",
    )
    assert _same_as_full(entry, carry) == (2, 2)


@needs_pyright
def test_carried_answers_match_a_full_check_for_a_type_reached_through_a_signature(
    entry: Path, tmp_path: Path
) -> None:
    """With real pyright: changing a type read by the signature of a used name (a field of a record) in another module re-checks consumers, and the carried answer equals the full-run answer."""
    pkg = entry / "pkg"
    (pkg / "kinds.hy").write_text(
        '(require doeff-hy.record [defrecord])\n(import dataclasses [dataclass])\n(defrecord Size "size" (#^ int n))\n',
        encoding="utf-8",
    )
    (pkg / "base.hy").write_text(
        _module(
            "(import pkg.kinds [Size])\n(defk measure [text]\n  {:pre [(: text str)] :post [(: % Size)]}\n  (Size :n (len text)))"
        ),
        encoding="utf-8",
    )
    (pkg / "user.hy").write_text(
        _module(
            "(import pkg.base [measure])\n(defk total [text]\n  {:pre [(: text str)] :post [(: % int)]}\n  (<- size (measure text))\n  (. size n))"
        ),
        encoding="utf-8",
    )
    carry = tmp_path / "carry.json"
    assert _same_as_full(entry, carry) == (5, 0)
    (pkg / "kinds.hy").write_text(
        (pkg / "kinds.hy").read_text(encoding="utf-8").replace("#^ int n", "#^ str n"),
        encoding="utf-8",
    )
    assert [d for d in _real(entry) if d["path"] == "pkg/user.hy" and d["severity"] == "error"], (
        "changing field n to str should produce an error in total"
    )
    assert _same_as_full(entry, carry) == (
        3,
        2,
    )  # kinds, base which reads Size in its signature, and user which uses measure (__init__ and alone are carried)


@needs_pyright
def test_carried_answers_match_a_full_check_when_a_missing_module_appears(
    entry: Path, tmp_path: Path
) -> None:
    """With real pyright: adding a file that resolves a previously unresolved import re-checks without carrying the unresolved error."""
    pkg = entry / "pkg"
    (pkg / "user.hy").write_text(
        _module(
            "(import pkg.later [late])\n(defk shout [text]\n  {:pre [(: text str)] :post [(: % int)]}\n  (<- n (late text))\n  n)"
        ),
        encoding="utf-8",
    )
    carry = tmp_path / "carry.json"
    assert _same_as_full(entry, carry) == (4, 0)
    (pkg / "later.hy").write_text(
        _module("(defk late [text]\n  {:pre [(: text str)] :post [(: % int)]}\n  (len text))"),
        encoding="utf-8",
    )
    assert _same_as_full(entry, carry) == (
        2,
        3,
    )  # the added later, and user which now resolves (__init__, alone, and base are carried)
