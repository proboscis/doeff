"""tools/public_api_inventory.py counts the public names of a subpackage and tags each name as effect, handler, other, or unclassified.

The tool imports and reads each package in a child process. Here we build small modules to check how kinds are assigned
(marker, return type, return shape, name), check that every dir under packages/ is listed in the inventory exactly once,
and check how the tool reads real packages (doeff-time, doeff-adr, doeff-linter, doeff-vm-core): effects and handlers,
console scripts, entry points and Hy macros, Rust executables, and packages with no Python names.
"""

from __future__ import annotations

import collections
import importlib
import importlib.util
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from types import FunctionType, ModuleType

import pytest
from doeff_core_effects import ReadEnvironment, subprocess_handler

from doeff import run, with_handlers

ROOT = Path(__file__).resolve().parents[1]


def _load() -> ModuleType:
    """Load the module under tools/ (tools/ is not a package)."""
    spec = importlib.util.spec_from_file_location(
        "public_api_inventory", ROOT / "tools" / "public_api_inventory.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["public_api_inventory"] = module
    spec.loader.exec_module(module)
    return module


tool = _load()

SAMPLE = '''
from collections.abc import Callable
from pathlib import Path
from typing import Any

import inventory_other
from doeff import DoExpr, EffectBase, Pass, Resume, WithHandlerType, do, handler, with_handlers
from doeff.program import ProgramHandler


class PingEffect(EffectBase):
    """An effect."""


class SessionHandler:
    """Named like a handler, without the marker."""


class PongEffect:
    """Named like an effect, without being one."""


def ping() -> PingEffect:
    return PingEffect()


def ping_untyped():
    return PingEffect()


@handler
def ping_handler(effect: PingEffect, k):
    yield Pass()


def ping_handler_factory() -> ProgramHandler:
    return ping_handler


def ping_handler_untyped():
    return ping_handler


def raw_ping(effect, k):
    yield Pass()


@do
def flow():
    yield ping()


def helper(x):
    return x + 1


def size() -> int:
    return 3


class Step:
    """A value of a program's shape."""


def step() -> Step:
    return Step()


@do
def raw_ping_program(effect, k):
    yield Pass()


def ping_handler_vague() -> Any:
    return ping_handler


def helper_vague() -> Callable[..., Any]:
    return helper


def ping_handler_stack() -> list[ProgramHandler]:
    return [ping_handler]


def make_session() -> SessionHandler:
    return SessionHandler()


def not_a_ping_handler_factory() -> ProgramHandler:
    return lambda program: program


def not_a_ping_handler_stack() -> list[ProgramHandler]:
    return [ping_handler, helper]


def ping_handler_for(name: str) -> ProgramHandler:
    return ping_handler


def ping_handler_reading(name: str) -> ProgramHandler:
    if not isinstance(name, str):
        raise TypeError("name is a str")
    return ping_handler


def not_a_ping_handler_for(name: str) -> ProgramHandler:
    return raw_ping


@do
def flow_handled():
    return (yield WithHandlerType(ping_handler, flow()))


def _wrap(program):
    return ping_handler(program)


def flow_wrapped() -> DoExpr:
    return _wrap(flow())


@do
def flow_answering(effect, cont):
    return (yield Resume(cont, 1))


def flow_with_handlers() -> DoExpr:
    return with_handlers([ping_handler], flow())


def flow_given(given, program) -> DoExpr:
    return given(program)


def flow_elsewhere() -> DoExpr:
    return inventory_other.install(flow())


def ping_handler_with_state_dir() -> ProgramHandler:
    (Path.home() / "ping-state").mkdir()
    (Path.cwd() / "ping-scratch").mkdir()
    return ping_handler


LIMIT = 3
'''

LAZY = """
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from inventory_sample import PingEffect as LazyPing


def __getattr__(name):
    if name == "LazyPing":
        from inventory_sample import PingEffect

        return PingEffect
    raise AttributeError(name)
"""

#: An entry point that exposes names lazily through the _LAZY_EXPORTS table only (names of modules type tools cannot read,
#: such as Hy modules without a stub, have no `if TYPE_CHECKING:` import).
LAZY_TABLE = """
from importlib import import_module

_LAZY_EXPORTS: dict[str, str] = {"LIMIT": "inventory_sample"}


def __getattr__(name):
    if name in _LAZY_EXPORTS:
        return getattr(import_module(_LAZY_EXPORTS[name]), name)
    raise AttributeError(name)
"""

#: A function in another package that installs a handler (the helper check does not follow functions of another top-level package).
OTHER = """
def install(program):
    from inventory_sample import ping_handler

    return ping_handler(program)
"""


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[ModuleType]:
    """Import SAMPLE as a module in tmp_path (placed in a file so inspect.getsource can read it)."""
    (tmp_path / "inventory_sample.py").write_text(textwrap.dedent(SAMPLE), encoding="utf-8")
    (tmp_path / "inventory_lazy.py").write_text(textwrap.dedent(LAZY), encoding="utf-8")
    (tmp_path / "inventory_lazy_table.py").write_text(textwrap.dedent(LAZY_TABLE), encoding="utf-8")
    (tmp_path / "inventory_other.py").write_text(textwrap.dedent(OTHER), encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        yield importlib.import_module("inventory_sample")
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("inventory_sample", None)
        sys.modules.pop("inventory_lazy", None)
        sys.modules.pop("inventory_lazy_table", None)
        sys.modules.pop("inventory_other", None)


@pytest.mark.parametrize(
    ("name", "kind", "form"),
    [
        ("PingEffect", "effect", "effect class"),
        ("ping", "effect", "function making an effect"),
        ("ping_untyped", "effect", "function making an effect"),
        ("ping_handler", "handler", "handler"),
        ("ping_handler_factory", "handler", "function returning a handler"),
        ("ping_handler_untyped", "handler", "function returning a handler"),
        ("raw_ping", "handler", "bare handler function"),
        ("flow", "other", "program helper"),
        ("flow_handled", "other", "function returning a program that installs a handler"),
        ("flow_wrapped", "other", "function returning a program that installs a handler"),
        ("flow_answering", "other", "function returning a program that answers an effect"),
        ("flow_with_handlers", "other", "function returning a program that installs a handler"),
        ("size", "other", "conversion function"),
        ("step", "other", "builder"),
        ("LIMIT", "other", "constant"),
        ("SessionHandler", "unclassified", "class"),
        ("PongEffect", "unclassified", "class"),
        ("helper", "unclassified", "function"),
        ("raw_ping_program", "handler", "bare handler function"),
        ("ping_handler_vague", "handler", "function returning a handler"),
        ("helper_vague", "unclassified", "function"),
        ("ping_handler_stack", "handler", "function returning a handler stack"),
        ("make_session", "unclassified", "function"),
        ("not_a_ping_handler_factory", "other", "function not returning a handler"),
        ("not_a_ping_handler_stack", "other", "function not returning a handler"),
        ("ping_handler_for", "handler", "function returning a handler"),
        ("ping_handler_reading", "handler", "function returning a handler"),
        ("not_a_ping_handler_for", "other", "function not returning a handler"),
    ],
)
def test_names_are_typed_by_marker_return_type_and_return_shape(
    sample: ModuleType, name: str, kind: str, form: str
) -> None:
    """Decide effect and handler from the marker, return type, and return shape; mark undecidable names unclassified.

    A return type that says nothing about the content (Any, Callable[..., Any]) is left to the return shape; if that does
    not decide either, the name is unclassified.
    A function read as returning a handler (a factory) is called with minimal arguments (none, or a placeholder for each
    argument without a default); if it returns an unmarked value it becomes "a function that does not return a handler"
    (ADR-DOE-API-001 R3: even if the return type says ProgramHandler, a function that passes the program through when
    called, or a name that returns an unmarked (effect, k) function, is not a factory). A factory that raises when called
    keeps the reading from its return type.
    """
    verdict = tool._Classifier().classify(sample.__dict__[name], name)
    assert (verdict.kind, verdict.form) == (kind, form)


@pytest.mark.parametrize(
    ("name", "said"),
    [
        ("ping_handler_factory", "with no arguments, calling it returns a handler"),
        ("ping_handler_stack", "with no arguments, calling it returns a handler"),
        ("ping_handler_untyped", "with no arguments, calling it returns a handler"),
        ("ping_handler_for", "with placeholders for the required arguments name, calling it returns a handler"),
        ("ping_handler_reading", "with placeholders for the required arguments name, calling it raises TypeError: name is a str"),
        ("not_a_ping_handler_factory", "with no arguments, calling it returns a function"),
        ("not_a_ping_handler_stack", "with no arguments, calling it returns a list"),
        ("not_a_ping_handler_for", "with placeholders for the required arguments name, calling it returns a function"),
    ],
)
def test_a_factory_says_whether_its_call_was_checked(
    sample: ModuleType, name: str, said: str
) -> None:
    """A factory row's reason says how it was called (no arguments, placeholder) and whether the marker was checked or the call raised."""
    verdict = tool._Classifier().classify(sample.__dict__[name], name)
    assert said in verdict.reason


@pytest.mark.parametrize(
    ("name", "said"),
    [
        (
            "flow",
            "a @do / defk function (calling it builds a program); it installs no handler inside and answers no effect",
        ),
        ("flow_handled", "flow_handled reads WithHandlerType"),
        ("flow_wrapped", "return type DoExpr; _wrap reads ping_handler"),
        ("flow_answering", "flow_answering reads Resume"),
    ],
)
def test_a_program_function_says_whether_it_is_a_helper(
    sample: ModuleType, name: str, said: str
) -> None:
    """A program-returning function's reason says whether it is a helper (neither it nor the same-package functions it reads
    install a handler or answer an effect); if it goes beyond a helper, which function reads which name (ADR-DOE-API-001 R18).
    """
    verdict = tool._Classifier().classify(sample.__dict__[name], name)
    assert said in verdict.reason


@pytest.mark.parametrize("name", ["flow_given", "flow_elsewhere"])
def test_what_the_helper_reading_does_not_see(sample: ModuleType, name: str) -> None:
    """Shapes the helper check does not see come out as a program helper even though they install a handler (ADR-DOE-API-001 R18; this pins the current limit).

    These are a function that applies a handler received as an argument, and a function whose handler is installed by a function in another top-level package. The check looks at inner names as global values or attribute names and follows only same-package functions, so it cannot see these two. Helpers are allowed name by name, and a person reading inside the allowed names is the safeguard.
    Once the check can see them, change this expectation to the beyond-helper form.
    """
    verdict = tool._Classifier().classify(sample.__dict__[name], name)
    assert (verdict.kind, verdict.form) == ("other", tool.PROGRAM_HELPER)


#: Three program functions that read the same callee, shared (to count whether the helper check judges shared only once).
SHARED = """
from doeff import DoExpr, do
from inventory_sample import ping


@do
def shared():
    yield ping()


def first() -> DoExpr:
    return shared()


def second() -> DoExpr:
    return shared()


@do
def third():
    yield shared()
"""


def test_the_helper_reading_judges_each_body_once(
    sample: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """However many program-returning functions read the same callee, within one reading (one _Classifier) the functions read
    by each body are collected only once, and whether it goes beyond a helper is judged only once per name and value
    (previously the callees were re-traced per function, and the daily run's doeff-cluster inventory exceeded 100 seconds).
    The verdict stays helper for all three.
    """
    (tmp_path / "inventory_shared.py").write_text(textwrap.dedent(SHARED), encoding="utf-8")
    shared = importlib.import_module("inventory_shared")
    read_bodies: collections.Counter[str] = collections.Counter()
    judged_names: collections.Counter[str] = collections.Counter()
    read_functions = tool._read_functions
    judged = tool._Classifier._judged_breach_form

    def counted_reads(function: FunctionType, package: str) -> Iterator[FunctionType]:
        read_bodies[function.__qualname__] += 1
        return read_functions(function, package)

    def counted_judgment(self: object, name: str, value: object) -> str | None:
        judged_names[name] += 1
        return judged(self, name, value)

    monkeypatch.setattr(tool, "_read_functions", counted_reads)
    monkeypatch.setattr(tool._Classifier, "_judged_breach_form", counted_judgment)
    classifier = tool._Classifier()
    try:
        forms = [
            classifier.classify(shared.__dict__[name], name).form
            for name in ("first", "second", "third")
        ]
    finally:
        sys.modules.pop("inventory_shared", None)
    assert forms == [tool.PROGRAM_HELPER] * 3
    assert read_bodies == {"first": 1, "second": 1, "third": 1, "shared": 1}
    assert judged_names["shared"] == 1
    assert set(judged_names.values()) == {1}


def test_a_factory_is_called_with_home_and_cwd_in_a_scratch_dir(
    sample: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A factory that creates a state dir when called does not create it in the HOME and cwd of whoever builds the inventory (they point to a temporary dir only during the call)."""
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / "state"))
    monkeypatch.chdir(work)
    verdict = tool._Classifier().classify(
        sample.ping_handler_with_state_dir, "ping_handler_with_state_dir"
    )
    assert (verdict.kind, verdict.form) == ("handler", "function returning a handler")
    assert "with no arguments, calling it returns a handler" in verdict.reason
    assert list(home.iterdir()) == []
    assert list(work.iterdir()) == []
    assert Path.cwd() == work
    restored = run(with_handlers([subprocess_handler], ReadEnvironment(("HOME", "XDG_STATE_HOME"))))
    assert tuple(entry.value for entry in restored) == (str(home), str(home / "state"))


def test_names_made_on_demand_are_exported(sample: ModuleType) -> None:
    """Names exposed lazily through the module's __getattr__ are also counted, from the names listed by the `if TYPE_CHECKING:` import."""
    lazy = importlib.import_module("inventory_lazy")
    names, read = tool._exported_names(lazy, tool._Sources(("inventory_lazy", "inventory_sample")))
    assert (names, read) == (["LazyPing"], "own")


def test_a_value_named_only_by_the_lazy_table_is_defined_where_the_table_points(sample: ModuleType) -> None:
    """For a value the entry point exposes only through the _LAZY_EXPORTS table (a name with no type import), the definition
    location is the definition line in the module the table points to, not the entry point's file, so a name moved to the
    entry point reads as the same row as the known table's submodule row (moved_to_root in
    tests/test_public_api_known_violations.py)."""
    lazy = importlib.import_module("inventory_lazy_table")
    sources = tool._Sources(("inventory_lazy_table", "inventory_sample"))
    assert lazy.LIMIT == sample.LIMIT
    assert sources.origin("inventory_lazy_table", "LIMIT") == sources.origin("inventory_sample", "LIMIT")
    assert sources.origin("inventory_sample", "LIMIT").module == "inventory_sample"


def test_every_package_dir_is_listed() -> None:
    """Every dir under packages/ with a pyproject.toml or Cargo.toml goes into the inventory's packages, except the parts
    inside doeff (doeff_vm, imported only by doeff). Parts are not counted, like the doeff root (ADR-DOE-API-001 R11)."""
    listed = {path.name for path in tool.package_dirs(ROOT)}
    parts = {part.dir for part in tool.DOEFF_PARTS}
    expected = {
        path.name
        for path in (ROOT / "packages").iterdir()
        if (path / "pyproject.toml").is_file() or (path / "Cargo.toml").is_file()
    }
    assert parts <= expected
    assert expected - parts <= listed
    assert not listed & parts


def test_markdown_has_one_section_per_package() -> None:
    """The table markdown has one heading per inventory package (the number of packages matches the number of sections)."""
    packages = [
        {"dir": name, "import_roots": [], "notes": [], "modules": [], "exports": [], "error": None}
        for name in ("doeff-a", "doeff-b", "doeff-c")
    ]
    page = tool.render_markdown(packages, "0123456789ab")
    assert [line for line in page.splitlines() if line.startswith("## doeff-")] == [
        "## doeff-a",
        "## doeff-b",
        "## doeff-c",
    ]


# Time limit in seconds for the check that imports and reads real packages in child processes (about 30 seconds for 4
# packages at load around 170; the default 60 seconds times out mid-fixture on heavily loaded machines).
_REAL_PACKAGES_BUDGET = 900


@pytest.fixture(scope="module")
def real_packages() -> dict[str, dict]:
    """Read the 4 real packages in child processes, as the tool does (from package dir name to inventory entry)."""
    names = ["doeff-time", "doeff-adr", "doeff-linter", "doeff-vm-core"]
    return {package["dir"]: package for package in tool.inventory(ROOT, names, jobs=4)}


def _rows(package: dict) -> dict[tuple[str, str], dict]:
    """Map (exporting module, name) to the inventory row, so rows can be looked up by name."""
    return {(row["module"], row["name"]): row for row in package["exports"]}


@pytest.mark.timeout(_REAL_PACKAGES_BUDGET)
def test_real_package_effects_and_handlers(real_packages: dict[str, dict]) -> None:
    """Read doeff-time's effect classes, effect-making functions, and handler-returning functions, with definition locations."""
    package = real_packages["doeff-time"]
    assert package["error"] is None
    rows = _rows(package)
    assert rows[("doeff_time", "GetTimeEffect")]["kind"] == "effect"
    assert rows[("doeff_time", "GetTimeEffect")]["defined_at"].startswith(
        "packages/doeff-time/src/doeff_time/_effects/time.py:"
    )
    assert rows[("doeff_time", "get_time")]["kind"] == "effect"
    assert rows[("doeff_time", "sim_time_handler")]["kind"] == "handler"
    assert rows[("doeff_time", "SimClock")]["kind"] == "other"


@pytest.mark.timeout(_REAL_PACKAGES_BUDGET)
def test_real_package_scripts_entry_points_and_macros(
    real_packages: dict[str, dict],
) -> None:
    """Count doeff-adr's console script, pytest entry point, and Hy macro."""
    rows = _rows(real_packages["doeff-adr"])
    assert rows[("(console script)", "doeff-adr")]["form"] == "console script"
    assert rows[("(entry point)", "pytest11:doeff_adr")]["form"] == "entry point(pytest11)"
    assert rows[("doeff_adr.macros", "defadr")]["form"] == "Hy macro"


@pytest.mark.timeout(_REAL_PACKAGES_BUDGET)
def test_rust_and_python_less_packages_are_listed(
    real_packages: dict[str, dict],
) -> None:
    """A package with only a Rust executable and a package with no Python names are also listed in the inventory."""
    linter = _rows(real_packages["doeff-linter"])
    assert linter[("(executable file)", "doeff-linter")]["form"] == "Rust executable"
    core = real_packages["doeff-vm-core"]
    assert core["exports"] == []
    assert core["notes"]
