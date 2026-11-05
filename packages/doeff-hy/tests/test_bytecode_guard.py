"""Counterexamples for the wrapper (doeff_hy_bytecode_guard) that stops using stale bytecode of a Hy module whose macros changed.

Shape of #1003: even if the provider of a macro changes, the user module's .pyc is judged valid by Python's check, which only looks at the source's mtime and size,
and it keeps running with the old expansion. With the wrapper, the user runs with the new expansion.

Hy is used at its **PyPI version** (not a fork — Hy is not modified) in an
disposable environment separated from this venv (``uv run --no-project --isolated --with hy==<version in uv.lock>``). This venv installs the
wrapper from a .pth at startup (test_the_venv_installs_the_guard_at_startup_before_hy), so it cannot be measured here separately from the no-wrapper form.
The version is read from the hy line of the repo root's uv.lock, at the one place in doeff that reads the uv.lock version (locked_version in scripts/semgrep_locked.py).
With a copied constant, these tests would keep measuring the old version even after the Hy in uv.lock is bumped. A change to uv.lock makes
`make test-changed` select this file ([[tool.doeff.contract-tests]] in the root pyproject.toml).

Even in non-import forms the wrapper depends on Hy internals, and this file measures it by running the wrapper on that Hy version (outside the population of R7 of ADR-DOE-HY-008):
the names of the module's macro table (``records.MACRO_TABLE``) and the fact that Hy's expansion looks that table up with ``name in table`` and ``table[name]``
(picked up by the table that remembers names looked up and names missing from the table — the test for macro changes and the test for a macro being added whose name the user calls), the names of the reader macro table
(``records.READER_TABLE`` — the test for reader macro changes), the table of public macro names written by Hy's export (read by the whole-require judgment),
and the shape of binding names rewritten by gensym and let (read by the canonicalization at the compile entry point — the test for gensym counting). If any of them changes, the wrapper will either
fail to record the macros it used and silently use a stale expansion, or the code will fluctuate with compile order.

The definition site of Hy's internal names is one place, ``records`` (``MACRO_TABLE``, ``READER_TABLE``, ``EXPORT_NAMES``, ``GENERATED_NAME_PREFIX``,
``ANON_VAR_NAME``), and users outside the wrapper (the analysis key of doeff_effect_analyzer._result_cache, the base-point key of doeff_hy._static_baseline,
and the whole-require judgment of macro_use) also draw from there. The last 2 tests of this file apply records' readings to a real Hy
module: the readings of table and export names agree with the behavior of Hy's own ``(require provider *)``, and the form Hy assigns to temporary names
stays one that ``fold_anon_var_numbers`` can fold. If Hy renames things, this goes red. The scan in
test_hy_internal_names_have_one_home.py turns red if the literals are copied outside records.

Tests that run the wrapper in the same process (store, entry-point wrapper, type-check expansion) measure the venv's Hy. In a work tree where the venv's Hy is not the uv.lock version (Hy was
bumped without a sync), any of them could go green without measuring the uv.lock version, so all tests in the file first check that the versions match
(:func:`_venv_hy_is_the_locked_version`, B). The contents of a PyPI version do not change per version,
so the same version measures the same Hy. When adding a dependency on Hy internals to the wrapper, put the test of that behavior in this file.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import marshal
import os
import shutil
import subprocess
import sys
import types
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from doeff_hy_bytecode_guard import _records as records

if TYPE_CHECKING:
    # collections.abc.Buffer exists from Python 3.12 — it is used only in annotations, so loading the module does not fail on 3.11.
    from collections.abc import Buffer

#: The repo root that holds the uv.lock deciding which Hy version to measure, and the one place in doeff that reads the uv.lock version.
ROOT = Path(__file__).resolve().parents[3]
LOCK_READER = ROOT / "scripts" / "semgrep_locked.py"
#: Parent of the wrapper's package (src of doeff-hy) — passed to the disposable environment by path (all of doeff-hy is not installed).
GUARD_ROOT = Path(__file__).resolve().parents[1] / "src"

#: Environment variables not passed to the disposable environment (this suite's fixed PYTHONDONTWRITEBYTECODE=1 prevents writing .pyc,
#: so the child could not write .pyc and the #1003 shape would not arise. A venv setting prevents uv from creating the disposable environment).
_ENV_NOT_PASSED = (
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONPYCACHEPREFIX",
    "PYTHONPATH",
)


@cache
def _locked_hy_version() -> str:
    """The Hy version decided by the repo root's uv.lock. If there is not exactly one hy line or the version is not one installed from the index (a git fork, etc.), the reader
    stops, naming the reason (SystemExit — the version to measure cannot be decided)."""
    spec = importlib.util.spec_from_file_location("semgrep_locked", LOCK_READER)
    assert spec is not None
    assert spec.loader is not None
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    return reader.locked_version(ROOT, "hy")


@pytest.fixture(autouse=True)
def _venv_hy_is_the_locked_version() -> None:
    """The tests in this file measure the wrapper with the Hy of the uv.lock version — first confirm that the venv's Hy, measured by same-process tests, is also that version."""
    import hy

    assert hy.__version__ == _locked_hy_version(), (
        f"the venv's Hy {hy.__version__} is not the uv.lock version {_locked_hy_version()} — same-process tests would not measure the uv.lock version,"
        " so run `make sync` before measuring"
    )


_LOADER = """\
import sys
sys.path.insert(0, {root!r})
guard = {guard!r}


def load_guard():
    # The wrapper's package is inside the checkout, so its .pyc must not be written into the checkout (check in the root conftest) —
    # load all the wrapper's modules first (the wrapper loads some modules the first time it meets Hy source. If the names were loaded one by one,
    # modules added later (expansion_trees, macro_hints) would be loaded for the first time under the setting that writes, and their .pyc was written into the checkout).
    import importlib
    import pkgutil
    sys.dont_write_bytecode = True
    sys.path.insert(0, {guard_root!r})
    import doeff_hy_bytecode_guard
    for module in pkgutil.iter_modules(doeff_hy_bytecode_guard.__path__, "doeff_hy_bytecode_guard."):
        importlib.import_module(module.name)
    doeff_hy_bytecode_guard.install()
    sys.dont_write_bytecode = False


if guard == "before-hy":
    load_guard()
import hy
if guard == "after-hy":
    load_guard()
print(hy.__version__)
{probe}
"""

#: Default check run in the disposable environment after installing the wrapper — import the user and print the expanded value.
_IMPORT_USER = "import pkg.user\nprint(pkg.user.value)\n"

#: A check that compiles the user twice in the same process (through the wrapper's compile entry point — like import, it adds the record and renumbers gensym names) and advances Hy's
#: gensym counter in between. The answer is whether the 2 codes are the same (code == — instructions, constants, names. marshal bytes are not compared because the
#: marks change with the number of object references) and the names and strings present in only one of them. The provider is loaded first
#: (if only the first compile wrapped the provider's load, the record marks could differ between the two).
_COMPILE_TWICE = """\
import importlib
import importlib.util
import json
import types
from doeff_hy_bytecode_guard._loader_hooks import source_to_code_as_import

importlib.import_module("pkg.macros")
spec = importlib.util.find_spec("pkg.user")


def compiled():
    with open(spec.origin, "rb") as source:
        return source_to_code_as_import(spec.loader, source.read(), spec.origin)


def texts(code):
    yield from (*code.co_names, *code.co_varnames, *code.co_cellvars, *code.co_freevars)
    for constant in code.co_consts:
        if isinstance(constant, types.CodeType):
            yield from texts(constant)
        elif isinstance(constant, str):
            yield constant


first = compiled()
for _ in range(100):
    hy.gensym()
second = compiled()
print(json.dumps({"same": first == second,
                  "differing": sorted(set(texts(first)) ^ set(texts(second)))}))
"""


def _write_package(root: Path) -> None:
    """Three things: the macro provider (macros), the helper the macro calls at expansion time (helpers), and the user (user)."""
    package = root / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "helpers.hy").write_text("(defn base [] 10)\n")
    (package / "macros.hy").write_text(
        "(import pkg.helpers [base])\n(defmacro answer [] (+ (base) 1))\n"
    )
    (package / "user.hy").write_text("(require pkg.macros [answer])\n(setv value (answer))\n")


def _edit(path: Path, text: str) -> None:
    """Rewrite the file and advance the mtime by 2 seconds — Python's own .pyc check looks at mtime in seconds, so rewriting in the same second with the same size
    would leave the provider's own .pyc stale yet judged valid (that is a limit of Python and is not the wrapper's concern)."""
    before = path.stat().st_mtime_ns
    path.write_text(text)
    later = before + 2_000_000_000
    os.utime(path, ns=(later, later))


def _run_on_pypi_hy(root: Path, guard: str, store: Path | None = None, guard_root: Path = GUARD_ROOT) -> int:
    """Import pkg.user in a disposable environment with PyPI's Hy and return the expanded value. The shared code store defaults to a place under root
    (so tests do not mix) — a test that spans work trees passes the same store. guard_root = parent of the wrapper's package (defaults to doeff-hy's
    src in this checkout — some tests pass a stand-in for a wrapper of a different version)."""
    return int(_probe_on_pypi_hy(root, guard, _IMPORT_USER, store, guard_root))


def _probe_on_pypi_hy(
    root: Path, guard: str, probe: str, store: Path | None = None, guard_root: Path = GUARD_ROOT
) -> str:
    """In a disposable environment with PyPI's Hy, run probe (Python statements — print the answer to stdout) after installing the wrapper, and return the answer text.
    The store and guard_root are the same as in :func:`_run_on_pypi_hy`."""
    uv = shutil.which("uv")
    assert uv is not None, "uv is not on PATH — cannot create the disposable environment for PyPI's Hy"
    script = root / f"load_{guard}.py"
    script.write_text(
        _LOADER.format(root=str(root), guard=guard, guard_root=str(guard_root), probe=probe)
    )
    # The child inherits this process's environment — remove only the names in _ENV_NOT_PASSED (`env -u`) and add the shared code store.
    unset = [flag for name in _ENV_NOT_PASSED for flag in ("-u", name)]
    code_store = store if store is not None else root / "code-store"
    python = f"{sys.version_info.major}.{sys.version_info.minor}"
    completed = subprocess.run(
        [
            "env",
            *unset,
            f"DOEFF_HY_CODE_STORE={code_store}",
            uv,
            "run",
            "--no-project",
            "--isolated",
            "--python",
            python,
            "--with",
            f"hy=={_locked_hy_version()}",
            "python",
            str(script),
        ],
        capture_output=True,
        text=True,
        cwd=root,
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    hy_version, answer = completed.stdout.split(maxsplit=1)
    assert hy_version == _locked_hy_version()
    return answer


def _user_pyc(root: Path) -> Path:
    [pyc] = (root / "pkg" / "__pycache__").glob("user.*.pyc")
    return pyc


def _stored_entries(store: Path | str, name: str, source: Path, data: bytes) -> tuple[str, ...]:
    """Entries under the code key of the source (contents data) of module name in the shared store (listed per macro environment — newest use first)."""
    from doeff_hy_bytecode_guard import _code_store as code_store, _loader_hooks as loader_hooks

    return code_store.code_entries(str(store), loader_hooks._store_key(name, str(source), data))


def test_pypi_hy_alone_keeps_the_old_expansion(tmp_path: Path) -> None:
    """Without the wrapper, the #1003 red shows up as is (confirms that this counterexample test is meaningful)."""
    _write_package(tmp_path)
    assert _run_on_pypi_hy(tmp_path, "none") == 11
    _edit(
        tmp_path / "pkg" / "macros.hy",
        "(import pkg.helpers [base])\n(defmacro answer [] (+ (base) 2))\n",
    )
    assert _run_on_pypi_hy(tmp_path, "none") == 11


@pytest.mark.parametrize("guard", ["before-hy", "after-hy"])
def test_a_macro_change_recompiles_the_user_on_pypi_hy(tmp_path: Path, guard: str) -> None:
    """After a macro changes, the user runs with the new expansion — whether the wrapper is installed before Hy is imported (.pth) or after."""
    _write_package(tmp_path)
    assert _run_on_pypi_hy(tmp_path, guard) == 11
    _edit(
        tmp_path / "pkg" / "macros.hy",
        "(import pkg.helpers [base])\n(defmacro answer [] (+ (base) 2))\n",
    )
    assert _run_on_pypi_hy(tmp_path, guard) == 12
    assert _run_on_pypi_hy(tmp_path, guard) == 12


def test_a_change_of_the_helper_a_macro_calls_recompiles_the_user(tmp_path: Path) -> None:
    """Even if a helper in the same package that the macro calls at expansion time changes, the user is rebuilt."""
    _write_package(tmp_path)
    assert _run_on_pypi_hy(tmp_path, "before-hy") == 11
    _edit(tmp_path / "pkg" / "helpers.hy", "(defn base [] 20)\n")
    assert _run_on_pypi_hy(tmp_path, "before-hy") == 21


def test_a_change_of_the_helper_a_macro_imports_inside_its_body_recompiles_the_user(
    tmp_path: Path,
) -> None:
    """Even if a helper in the same package that the macro imports inside its function at expansion time changes, the user is rebuilt (
    doeff-hy's defsystem imports ``doeff-hy.system-form`` during expansion. After a pin where only that helper changed, since the helper was not in the provider's
    namespace it was missing from the record, and an expansion importing the old store remained in the .pyc)."""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "helpers.hy").write_text("(defn base [] 10)\n")
    (package / "macros.hy").write_text(
        "(defmacro answer [] (import pkg.helpers [base]) (+ (base) 1))\n"
    )
    (package / "user.hy").write_text("(require pkg.macros [answer])\n(setv value (answer))\n")
    assert _run_on_pypi_hy(tmp_path, "before-hy") == 11
    _edit(package / "helpers.hy", "(defn base [] 20)\n")
    assert _run_on_pypi_hy(tmp_path, "before-hy") == 21


@pytest.mark.parametrize(("guard", "after"), [("none", 10), ("before-hy", 15)])
def test_a_reader_macro_change_recompiles_the_user_on_pypi_hy(
    tmp_path: Path, guard: str, after: int
) -> None:
    """After a reader macro changes, the user runs with the new expansion (without the wrapper the old expansion stays — confirms that this test measures the wrapper). The wrapper
    puts the reader macros of other modules found in the user module's reader macro table (``records.READER_TABLE``) into the record, so if Hy renames the table's
    name, they would be missing from the record and the old expansion would be used silently."""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    reader = "(defreader twice\n  (setv x (.parse-one-form &reader))\n  `(* {} ~x))\n"
    (package / "readers.hy").write_text(reader.format(2))
    (package / "user.hy").write_text(
        "(require pkg.readers :readers [twice])\n(setv value #twice 5)\n"
    )
    assert _run_on_pypi_hy(tmp_path, guard) == 10
    _edit(package / "readers.hy", reader.format(3))
    assert _run_on_pypi_hy(tmp_path, guard) == after


@pytest.mark.parametrize(("guard", "after"), [("none", 11), ("before-hy", 12), ("after-hy", 12)])
def test_a_macro_required_inside_a_function_recompiles_the_user_on_pypi_hy(
    tmp_path: Path, guard: str, after: int
) -> None:
    """Even after a macro required inside a function changes, the user runs with the new expansion (without the wrapper the old expansion stays — confirms that this test
    measures the wrapper). Hy's expansion looks up the function-local macro table (the compiler's local state) before the module's table, so a wrapper that only
    swapped the module's table would leave local macros out of the record and silently use the old expansion even after the macro changed.
    The wrapper reads the entry point where the compiler opens its local state, so if Hy changes that entry point
    this test goes red."""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "macros.hy").write_text("(defmacro answer [] 11)\n")
    (package / "user.hy").write_text(
        "(defn f []\n  (require pkg.macros [answer])\n  (defn g [] (answer))\n  (g))\n(setv value (f))\n"
    )
    assert _run_on_pypi_hy(tmp_path, guard) == 11
    _edit(package / "macros.hy", "(defmacro answer [] 12)\n")
    assert _run_on_pypi_hy(tmp_path, guard) == after
    assert _run_on_pypi_hy(tmp_path, guard) == after


@pytest.mark.parametrize("exports", ["", "(export :macros [answer])\n"], ids=["all", "exported"])
def test_a_new_macro_named_like_a_function_the_user_calls_recompiles_the_user_on_pypi_hy(
    tmp_path: Path, exports: str
) -> None:
    """When a macro named like a function that a whole-require user calls is added to the provider, the user is rebuilt and the call
    turns into a macro expansion. The wrapper remembers names the expansion looked up in the table and did not find (``name in table`` false) and judges the user stale once the provider adds them, so if Hy stops
    checking the table with ``in`` it would fail to remember. In the form where the provider narrows the public macros with Hy's export (exported), the whole-require judgment reads Hy's table of public
    names, so if Hy renames those names it would not count as a whole require and would miss the added macro (
    B)."""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    provider = exports + "(defmacro answer [] 1)\n(defmacro hidden [] 2)\n"
    (package / "macros.hy").write_text(provider)
    (package / "user.hy").write_text(
        "(require pkg.macros *)\n(defn shadow [x] x)\n(setv value (+ (answer) (shadow 10)))\n"
    )
    assert _run_on_pypi_hy(tmp_path, "before-hy") == 11
    _edit(
        package / "macros.hy",
        provider.replace("[answer]", "[answer shadow]") + "(defmacro shadow [x] 0)\n",
    )
    assert _run_on_pypi_hy(tmp_path, "before-hy") == 1


def test_the_code_does_not_depend_on_how_many_gensyms_the_process_made_on_pypi_hy(
    tmp_path: Path,
) -> None:
    """The code of the same source does not depend on how many times the process called gensym beforehand — the compile entry point renumbers gensym names (including those buried in binding names rewritten by let)
    into module order. The renumbering reads the shape of Hy's gensym and let names, so if Hy
    changes the shape, it would silently fail to renumber, and the fingerprints of .pyc, the shared store, and packed Programs would fluctuate with compile order."""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "macros.hy").write_text(
        '(defmacro bound [x]\n  (setv g (hy.gensym "bound"))\n  `(let [~g ~x] (+ ~g 1)))\n'
    )
    (package / "user.hy").write_text(
        "(require pkg.macros [bound])\n(defn f [] (bound 10))\n(setv value (f))\n"
    )
    answer = json.loads(_probe_on_pypi_hy(tmp_path, "before-hy", _COMPILE_TWICE))
    assert answer == {"same": True, "differing": []}


def test_the_record_lives_inside_a_standard_pyc(tmp_path: Path) -> None:
    """The record is inside the .pyc (a code constant), and __pycache__ holds only standard .pyc files (no neighboring files like .hydeps are written)."""
    _write_package(tmp_path)
    _run_on_pypi_hy(tmp_path, "before-hy")
    cache = tmp_path / "pkg" / "__pycache__"
    assert sorted(path.suffix for path in cache.iterdir()) == [".pyc"] * 4
    data = _user_pyc(tmp_path).read_bytes()
    assert data[:4] == importlib.util.MAGIC_NUMBER
    record = records.record_of(marshal.loads(data[records.PYC_HEADER_BYTES :]))
    assert record is not None
    assert record.hy_version == _locked_hy_version()
    # The record is the macros the expansion used (provider module name, macro name, digest) and holds no file path.
    assert [(used.table, used.module, used.name) for used in record.macros] == [
        (records.MACRO_TABLE, "pkg.macros", "answer")
    ]
    # The provider has only the macro answer, so even a require that selects names counts as a whole require (it only errs toward judging stale).
    assert record.providers == (records.WholeRequire("pkg.macros", ""),)


def _rewrite_as_hash_based(pyc: Path, source: Path, *, checked: bool) -> None:
    """Replace the .pyc header with the PEP 552 hash-based form (the form baked by the image-assembly tool = unchecked)."""
    data = pyc.read_bytes()
    flags = records.FLAG_HASH_BASED | (records.FLAG_CHECK_SOURCE if checked else 0)
    header = (
        importlib.util.MAGIC_NUMBER
        + flags.to_bytes(4, "little")
        + importlib.util.source_hash(source.read_bytes())
    )
    pyc.write_bytes(header + data[records.PYC_HEADER_BYTES :])


def test_a_checked_hash_pyc_is_recompiled_in_the_same_form(tmp_path: Path) -> None:
    """Also rebuild a hash-based .pyc that Python checks against the source, and rewrite it in the same form (checked-hash)."""
    _write_package(tmp_path)
    _run_on_pypi_hy(tmp_path, "before-hy")
    _rewrite_as_hash_based(_user_pyc(tmp_path), tmp_path / "pkg" / "user.hy", checked=True)
    _edit(
        tmp_path / "pkg" / "macros.hy",
        "(import pkg.helpers [base])\n(defmacro answer [] (+ (base) 2))\n",
    )
    assert _run_on_pypi_hy(tmp_path, "before-hy") == 12
    flags = int.from_bytes(_user_pyc(tmp_path).read_bytes()[4:8], "little")
    assert flags == records.FLAG_HASH_BASED | records.FLAG_CHECK_SOURCE


def test_an_unchecked_hash_pyc_is_trusted_like_python_trusts_it(tmp_path: Path) -> None:
    """A .pyc that Python does not check against the source (image form) does not check macro changes either — it assumes it is rebuilt during assembly."""
    _write_package(tmp_path)
    _run_on_pypi_hy(tmp_path, "before-hy")
    _rewrite_as_hash_based(_user_pyc(tmp_path), tmp_path / "pkg" / "user.hy", checked=False)
    _edit(
        tmp_path / "pkg" / "macros.hy",
        "(import pkg.helpers [base])\n(defmacro answer [] (+ (base) 2))\n",
    )
    assert _run_on_pypi_hy(tmp_path, "before-hy") == 11


def _counting_macros(add: int) -> str:
    """Contents of macros.hy where the macro writes one line to expansions.log under root on every expansion (counts whether the conversion was redone)."""
    return (
        "(import pkg.helpers [base])\n"
        '(defmacro answer [] (with [f (open "expansions.log" "a")] (.write f "x\\n"))'
        f" (+ (base) {add}))\n"
    )


def _write_counting_package(root: Path, add: int) -> None:
    """The same 3 things as _write_package, but the macro writes one line to expansions.log under root on every expansion (counts whether the conversion was redone)."""
    _write_package(root)
    (root / "pkg" / "macros.hy").write_text(_counting_macros(add))


def _expansions(root: Path) -> int:
    log = root / "expansions.log"
    return len(log.read_text().splitlines()) if log.exists() else 0


def test_a_new_tree_with_the_same_sources_reuses_the_code_without_expanding(tmp_path: Path) -> None:
    """A new work tree (same contents, different absolute path, no .pyc) uses the code in the shared store and does not expand the macro again.
    The record holds no file path, so the .pyc records of the 2 trees are the same. The tree's .pyc is also written."""
    store = tmp_path / "store"
    first, second = tmp_path / "first", tmp_path / "second"
    for root in (first, second):
        root.mkdir()
        _write_counting_package(root, 1)
    assert _run_on_pypi_hy(first, "before-hy", store) == 11
    assert _expansions(first) == 1
    assert _run_on_pypi_hy(second, "before-hy", store) == 11
    assert _expansions(second) == 0
    record = records.record_of(
        marshal.loads(_user_pyc(second).read_bytes()[records.PYC_HEADER_BYTES :])
    )
    assert record is not None
    assert record == records.record_of(
        marshal.loads(_user_pyc(first).read_bytes()[records.PYC_HEADER_BYTES :])
    )


def test_a_new_tree_with_a_different_macro_does_not_reuse_the_other_trees_expansion(
    tmp_path: Path,
) -> None:
    """Even for the same user, a tree with different macro contents does not use code made in another tree — it checks against the current tree's
    provider rather than the file of the tree that made the record (even if the macro of the tree that made it remains unchanged)."""
    store = tmp_path / "store"
    first, other = tmp_path / "first", tmp_path / "other"
    first.mkdir()
    other.mkdir()
    _write_counting_package(first, 1)
    _write_counting_package(other, 5)
    assert _run_on_pypi_hy(first, "before-hy", store) == 11
    assert _run_on_pypi_hy(other, "before-hy", store) == 15
    assert _expansions(other) == 1


def test_two_macro_versions_taking_turns_on_one_store_stop_expanding_from_the_second_round(tmp_path: Path) -> None:
    """Two writers with different macro contents (a conversation work tree with a different doeff version and the stand-in for runtime-environment preparation — the user's source is the same) take turns
    reading new trees in the same store. With one entry per key, each saw the other's entry as stale, expanded again, and overwrote the same key (2026-10-12
    zeus — 100 to 600 rebuilds per preparation). With one key holding an entry per macro environment, from the second round
    neither expands again."""
    store = tmp_path / "store"
    for turn in (1, 2, 3):
        for name, add in (("one", 1), ("five", 5)):
            root = tmp_path / f"{name}-{turn}"
            root.mkdir()
            _write_counting_package(root, add)
            assert _run_on_pypi_hy(root, "before-hy", store) == 10 + add
            assert _expansions(root) == (1 if turn == 1 else 0), (name, turn)


def _guard_of_the_next_record_generation(tmp_path: Path) -> Path:
    """Parent dir of a copy of this checkout's wrapper in which only the record tag (RECORD_TAG) is bumped to the next generation — a stand-in for a writer of a different doeff-hy version (writers whose record
    format generations differ cannot read each other's entry records)."""
    root = tmp_path / "guard-next-generation"
    shutil.copytree(
        GUARD_ROOT / "doeff_hy_bytecode_guard",
        root / "doeff_hy_bytecode_guard",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    records_file = root / "doeff_hy_bytecode_guard" / "_records.py"
    current = f'RECORD_TAG = "{records.RECORD_TAG}"'
    text = records_file.read_text(encoding="utf-8")
    assert text.count(current) == 1, "the record tag line is not exactly one in records.py — cannot make the next-generation stand-in"
    prefix, number = records.RECORD_TAG.rsplit("/", 1)
    records_file.write_text(text.replace(current, f'RECORD_TAG = "{prefix}/{int(number) + 1}"'), encoding="utf-8")
    return root


def test_two_record_generations_taking_turns_on_one_store_keep_each_others_entries(tmp_path: Path) -> None:
    """Two writers with different record tags (RECORD_TAG) (a work tree with a different doeff-hy version and the stand-in for runtime-environment preparation — they cannot read each other's entry records)
    take turns reading new trees in the same store. If a reader dropped entries of an unreadable generation as "stale", they would delete each other's entries and expand every time
    (the doeff-hy of the 2 writers cannot be told apart by the same test). With one key holding an entry per environment,
    neither expands again from the second round."""
    store = tmp_path / "store"
    writers = (("this", GUARD_ROOT), ("next", _guard_of_the_next_record_generation(tmp_path)))
    for turn in (1, 2, 3):
        for name, guard_root in writers:
            root = tmp_path / f"{name}-{turn}"
            root.mkdir()
            _write_counting_package(root, 1)
            assert _run_on_pypi_hy(root, "before-hy", store, guard_root) == 11
            assert _expansions(root) == (1 if turn == 1 else 0), (name, turn)


def _user_entries(store: Path, root: Path) -> tuple[str, ...]:
    """Entries under the key of root tree's pkg.user in the store (listed per macro environment)."""
    user = root / "pkg" / "user.hy"
    return _stored_entries(store, "pkg.user", user, user.read_bytes())


def test_a_stale_pyc_in_a_reused_tree_takes_the_stores_matching_entry_without_compiling(tmp_path: Path) -> None:
    """A .pyc in a reused work tree has a header (mtime and size) matching the source, and only its record does not match the current environment's macros (a tree where the sibling doeff was swapped — the deploy
    declaration tree swaps doeff on every declaration). If the store has an entry matching the current environment, use it without compiling (= expanding) again, and rewrite the .pyc too.
    Without the lookup, the first import recompiles all the tree's Hy modules (2026-10-12 — collecting one test took about 880 modules and about 670 CPU seconds)."""
    store = tmp_path / "store"
    reused, other = tmp_path / "reused", tmp_path / "other"
    for root, add in ((reused, 1), (other, 5)):
        root.mkdir()
        _write_counting_package(root, add)
    assert _run_on_pypi_hy(reused, "before-hy", store) == 11
    assert _run_on_pypi_hy(other, "before-hy", store) == 15  # an entry for the add = 5 environment is added to the store
    assert (_expansions(reused), _expansions(other)) == (1, 1)
    # The reused tree's macro environment becomes the same as other's — user.hy's .pyc header still matches, and only the record becomes stale.
    _edit(reused / "pkg" / "macros.hy", _counting_macros(5))
    assert _run_on_pypi_hy(reused, "before-hy", store) == 15
    assert _expansions(reused) == 1  # used the store's entry — did not expand again
    # The .pyc was rewritten with the store's code — even with an empty store it does not expand again.
    assert _run_on_pypi_hy(reused, "before-hy", tmp_path / "empty-store") == 15
    assert _expansions(reused) == 1


def test_a_stale_checked_hash_pyc_takes_the_stores_matching_entry_and_is_rewritten_in_the_same_form(
    tmp_path: Path,
) -> None:
    """A hash-based .pyc that Python checks against the source also uses the store's matching entry when its record is stale, and is rewritten in the same form (checked-hash)."""
    store = tmp_path / "store"
    reused, other = tmp_path / "reused", tmp_path / "other"
    for root, add in ((reused, 1), (other, 5)):
        root.mkdir()
        _write_counting_package(root, add)
    assert _run_on_pypi_hy(reused, "before-hy", store) == 11
    assert _run_on_pypi_hy(other, "before-hy", store) == 15
    _rewrite_as_hash_based(_user_pyc(reused), reused / "pkg" / "user.hy", checked=True)
    _edit(reused / "pkg" / "macros.hy", _counting_macros(5))
    assert _run_on_pypi_hy(reused, "before-hy", store) == 15
    assert _expansions(reused) == 1
    flags = int.from_bytes(_user_pyc(reused).read_bytes()[4:8], "little")
    assert flags == records.FLAG_HASH_BASED | records.FLAG_CHECK_SOURCE
    assert _run_on_pypi_hy(reused, "before-hy", tmp_path / "empty-store") == 15
    assert _expansions(reused) == 1


def test_a_stale_pyc_with_no_matching_entry_in_the_store_compiles_and_adds_the_entry(tmp_path: Path) -> None:
    """When the store has no entry matching the current environment, compile again as before and add that code to the store (so a later tree in the same environment can use it)."""
    store = tmp_path / "store"
    reused, other = tmp_path / "reused", tmp_path / "other"
    reused.mkdir()
    other.mkdir()
    _write_counting_package(reused, 1)
    _write_counting_package(other, 7)
    assert _run_on_pypi_hy(reused, "before-hy", store) == 11
    before = _user_entries(store, reused)
    _edit(reused / "pkg" / "macros.hy", _counting_macros(7))  # the store has no entry for add = 7
    assert _run_on_pypi_hy(reused, "before-hy", store) == 17
    assert _expansions(reused) == 2  # compiled again
    assert len(_user_entries(store, reused)) == len(before) + 1
    assert _run_on_pypi_hy(other, "before-hy", store) == 17  # another tree in the same environment uses the added entry
    assert _expansions(other) == 0


def test_a_pyc_carried_from_another_tree_is_checked_against_the_current_trees_macro(
    tmp_path: Path,
) -> None:
    """Runtime-environment preparation carries over the previous root's .pyc by hardlink for files whose source hash is the same. The carried .pyc's record
    names the previous root's macro file, so even if the previous root's macro remains unchanged, it is rebuilt when the current tree's macro differs.
    Checking against the path in the record would keep using the previous root's expansion (11)."""
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _write_counting_package(first, 1)
    _write_counting_package(second, 5)
    assert _run_on_pypi_hy(first, "before-hy", tmp_path / "store-first") == 11
    carried = _user_pyc(first)
    _rewrite_as_hash_based(carried, first / "pkg" / "user.hy", checked=True)
    cache = second / "pkg" / "__pycache__"
    cache.mkdir()
    os.link(carried, cache / carried.name)
    assert _run_on_pypi_hy(second, "before-hy", tmp_path / "store-second") == 15
    assert _expansions(second) == 1


def test_a_file_changed_between_compile_and_the_store_write_does_not_file_old_code_under_the_new_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shared store's key is made from the very bytes that were compiled. Formerly the file was read again after compile
    to make the key, so if the file was rewritten in between (a landing git update, an import in the middle of an edit), code made from the old contents went under the new
    contents' key, and every work tree reading a file with the same contents ran with the old behavior (real case 2026-10-02 13:57 — the test for doeff-cluster's
    sim/local.hy was red every time)."""
    import hy  # noqa: F401 — load the entry point that compiles Hy source
    from doeff_hy_bytecode_guard import _code_store as code_store

    store = tmp_path / "store"
    monkeypatch.setenv(code_store.STORE_ENV, str(store))
    # This suite runs with PYTHONDONTWRITEBYTECODE=1 — the store is written only when the setting writes bytecode, so allow writing only within this test.
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    package = tmp_path / "racepkg"
    package.mkdir()
    source = package / "mod.hy"
    old, new = b"(setv value 1)\n", b"(setv value 2)\n"
    source.write_bytes(old)
    name = "racepkg.mod"

    class FileChangedAfterCompile(importlib.machinery.SourceFileLoader):
        """A scenario where the source file is rewritten right after compile — reading the source after writing the compiled code's .pyc
        returns the new contents (the standard get_code writes the .pyc right after compile)."""

        mut_compiled = False

        def set_data(self, path: str, data: Buffer, *, _mode: int = 0o666) -> None:
            super().set_data(path, data, _mode=_mode)
            self.mut_compiled = True

        def get_data(self, path: str) -> bytes:
            if self.mut_compiled and path == str(source):
                return new
            return super().get_data(path)

    loader = FileChangedAfterCompile(name, str(source))
    spec = importlib.util.spec_from_file_location(name, str(source), loader=loader)
    assert spec is not None
    monkeypatch.setitem(sys.modules, name, importlib.util.module_from_spec(spec))
    assert loader.get_code(name) is not None
    assert loader.mut_compiled
    assert not _stored_entries(store, name, source, new), (
        "code made from the old contents went under the key of the new contents"
    )
    assert _stored_entries(store, name, source, old), (
        "not stored in the store under the key of the compiled contents"
    )


def _compile_in_fresh_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, package_name: str) -> Path:
    """Make one Hy module `<package_name>.mod` under tmp_path, compile it through the loader entry point while keeping the no-write setting (this suite's fixed one),
    and return the path of its source."""
    package = tmp_path / package_name
    package.mkdir()
    source = package / "mod.hy"
    source.write_bytes(b"(setv value 3)\n")
    name = f"{package_name}.mod"
    loader = importlib.machinery.SourceFileLoader(name, str(source))
    spec = importlib.util.spec_from_file_location(name, str(source), loader=loader)
    assert spec is not None
    monkeypatch.setitem(sys.modules, name, importlib.util.module_from_spec(spec))
    assert loader.get_code(name) is not None
    return source


def test_a_named_store_is_filled_even_when_bytecode_is_not_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the store dir is named by DOEFF_HY_CODE_STORE, the store is filled even under the setting that does not write .pyc into the tree.
    The tool that bakes the worker's bytecode runs with PYTHONDONTWRITEBYTECODE=1, so without filling, it converted its own dependency Hy from source
    on every startup (run twice with an empty store, both times about 14 to 17 CPU seconds and 0 files in the store — 2026-10-08)."""
    import hy  # noqa: F401 — load the entry point that compiles Hy source
    from doeff_hy_bytecode_guard import _code_store as code_store

    store = tmp_path / "store"
    monkeypatch.setenv(code_store.STORE_ENV, str(store))
    assert sys.dont_write_bytecode, "this test looks under the no-write setting (the suite's fixed one)"
    name = "namedstorepkg.mod"
    source = _compile_in_fresh_package(tmp_path, monkeypatch, "namedstorepkg")
    assert _stored_entries(store, name, source, source.read_bytes()), (
        "code compiled under the no-write setting did not go into the named store"
    )
    assert not (source.parent / "__pycache__").exists(), "a .pyc was written into the tree despite the no-write setting"


def test_the_default_store_is_not_filled_when_bytecode_is_not_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the store is not named (the default user cache dir), it is not filled under the no-write setting, as before — a process started
    under the no-write setting does not bloat the user's cache without naming a store."""
    import hy  # noqa: F401 — load the entry point that compiles Hy source
    from doeff_hy_bytecode_guard import _code_store as code_store

    monkeypatch.delenv(code_store.STORE_ENV, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    store = code_store.store_dir()
    assert store is not None
    name = "defaultstorepkg.mod"
    source = _compile_in_fresh_package(tmp_path, monkeypatch, "defaultstorepkg")
    assert not _stored_entries(store, name, source, source.read_bytes())


def test_the_venv_installs_the_guard_at_startup_before_hy() -> None:
    """In a venv with doeff-hy installed, every Python has the wrapper installed at startup (before Hy is imported) (.pth)."""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from doeff_hy_bytecode_guard._loader_hooks import installed;"
            " print(installed(), 'hy' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.split() == ["True", "False"]


def test_a_provider_first_loaded_by_the_type_check_expansion_is_not_reused_by_a_normal_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A macro provider first loaded by a require during the type-check expansion (doeff_hy._static_view) becomes non-executable code, without the :pre isinstance and
    the runtime import. If a macro dependency record is attached to it, it stays in the .pyc and the shared store under the same key as the normal expansion,
    and a later normal import read it and failed with ``NameError: name '_doeff_do' is not defined`` (reproduction of I-3 of milestone I,
    2026-10-03 — a normal run after doeff-hy-check). The type-check expansion's code does not go into the store, and a later normal import reads the expansion with :pre attached."""
    import hy  # noqa: F401 — load the entry point that compiles Hy source
    from doeff import run
    from doeff_hy._static_view import static_view
    from doeff_hy_bytecode_guard import _code_store as code_store

    store = tmp_path / "store"
    monkeypatch.setenv(code_store.STORE_ENV, str(store))
    # This suite runs with PYTHONDONTWRITEBYTECODE=1 — .pyc and the store are written only when the setting writes bytecode, so allow writing only within this test.
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    monkeypatch.setattr(sys, "pycache_prefix", str(tmp_path / "pyc"))
    package = tmp_path / "typecheckpkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    source = package / "provider.hy"
    source.write_text(
        "(require doeff-hy.macros [defk])\n"
        "(defmacro twice [x] `(+ ~x ~x))\n"
        "(defk checked [x]\n"
        '  {:pre [(: x int)] :post [(: % int)] :tags {:context "probe" :role "judgment"}}\n'
        '  "A function with a type contract (the :pre isinstance exists only in the normal expansion)."\n'
        "  x)\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    name = "typecheckpkg.provider"
    try:
    # The compile stage in the form where the type-check target's require loads for the first time (get_code inside placing the module — writes to .pyc and the store occur).
    # The type-check expansion's code is not executable, so it is not exec'd (even in doeff-hy-check, the require's exec fails with NameError).
        spec = importlib.util.find_spec(name)
        assert spec is not None and isinstance(spec.loader, importlib.machinery.SourceFileLoader)
        sys.modules[name] = importlib.util.module_from_spec(spec)
        with static_view():
            spec.loader.get_code(name)
        assert not _stored_entries(store, name, source, source.read_bytes()), (
            "the type-check expansion's code went into the shared store"
        )
        del sys.modules[name]
        provider = importlib.import_module(name)  # a normal import with the same .pyc location and the shared store
        with pytest.raises(AssertionError, match="pre-condition"):
            run(provider.checked("not-an-int"))
    finally:
        for loaded in (name, "typecheckpkg"):
            sys.modules.pop(loaded, None)


def test_a_file_loaded_again_by_path_under_another_name_records_the_macros_its_expansion_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In a process that loaded the same Hy file by package name, load it again by path under another name (the way the worker's bytecode tool loads the judgment module
    bake_plan.hy), and Hy looks up the compile's module from the file's name (``inspect.getmodule``), so the expansion runs with the module table of the
    module under the package name loaded first. If the record is made from the table of the module under the other name, that table is never consulted, an empty record with no used macros goes into the
    shared store, and even after the macro changed the check passes through and the old expansion was used (2026-10-11 atlas — the pytest for verifying doeff's intake
    wrote an entry for bake_plan.hy, patrol's preparation pulled it, and with the defrecord expansion before ec08cd77d
    ``seal_record() missing ... calls_post_init``). The record is made from the table the expansion actually consulted."""
    import hy  # noqa: F401 — load the entry point that compiles Hy source
    from doeff_hy_bytecode_guard import _code_store as code_store

    store = tmp_path / "store"
    monkeypatch.setenv(code_store.STORE_ENV, str(store))
    package = tmp_path / "aliaspkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "macros.hy").write_text("(defmacro answer [] 11)\n")
    source = package / "user.hy"
    source.write_text("(require aliaspkg.macros [answer])\n(setv value (answer))\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    alias = "aliaspkg_user_by_path"
    try:
        assert importlib.import_module("aliaspkg.user").value == 11
        loader = importlib.machinery.SourceFileLoader(alias, str(source))
        spec = importlib.util.spec_from_file_location(alias, str(source), loader=loader)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[alias] = module
        loader.exec_module(module)
        assert module.value == 11
        entries = _stored_entries(store, alias, source, source.read_bytes())
        assert len(entries) == 1, f"the code loaded by path under another name did not go into the shared store as exactly one entry: {entries}"
        record = records.record_of(marshal.loads(Path(entries[0]).read_bytes()))
        assert record is not None
        assert [(used.module, used.name) for used in record.macros] == [("aliaspkg.macros", "answer")], (
            f"the record does not name the macros the expansion used: {record}"
        )
    finally:
        for loaded in (alias, "aliaspkg.user", "aliaspkg.macros", "aliaspkg"):
            sys.modules.pop(loaded, None)


def test_a_hy_module_read_from_a_current_pyc_does_not_look_up_the_expansion_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Loading a Hy module whose work-tree .pyc matches the source does not compile, so it does not look up the module the expansion runs in (found from the file name with
    ``inspect.getmodule``, like Hy's compile entry point). The lookup walks all loaded modules, so doing it for each Hy module
    made a fixed cost of about 0.6 CPU seconds in loading a test (about 800 Hy modules, about 1500 modules) (measured 2026-10-11 —
    the fixed cost of test_emulated_screen.hy went from 3.5 to 4.1 seconds)."""
    import inspect

    import hy  # noqa: F401 — load the entry point that compiles Hy source
    from doeff_hy_bytecode_guard import _code_store as code_store

    monkeypatch.setenv(code_store.STORE_ENV, str(tmp_path / "store"))
    monkeypatch.setattr(sys, "dont_write_bytecode", False)  # this suite does not write .pyc — write only here and read on the second load
    package = tmp_path / "pycpkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "macros.hy").write_text("(defmacro answer [] 11)\n")
    source = package / "user.hy"
    source.write_text("(require pycpkg.macros [answer])\n(setv value (answer))\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    looked_up: list[str] = []
    getmodule = inspect.getmodule

    def counting_getmodule(target: object, _filename: str | None = None) -> object:
        if _filename == str(source):
            looked_up.append(_filename)
        return getmodule(target, _filename)

    try:
        assert importlib.import_module("pycpkg.user").value == 11
        assert looked_up == [], "looked up before counting started"  # only the second load is counted
        assert list((package / "__pycache__").glob("user.*.pyc")), "the first load did not write a .pyc"
        sys.modules.pop("pycpkg.user")
        monkeypatch.setattr(inspect, "getmodule", counting_getmodule)
        assert importlib.import_module("pycpkg.user").value == 11
        assert looked_up == [], f"a load that only reads a usable .pyc looked up the expansion module {len(looked_up)} times"
    finally:
        for loaded in ("pycpkg.user", "pycpkg.macros", "pycpkg"):
            sys.modules.pop(loaded, None)


#: The entry point called by ``hy <script>`` (runpy.run_path) and ``hy -m`` (runpy.run_module) — the hy command just passes arguments and calls this function.
_RUN_BY_HY_COMMAND = "from hy.cmdline import cmdline_handler\ncmdline_handler({argv!r})\n"


def _write_scripts(root: Path) -> Path:
    """To the user of _write_counting_package, add 2 scripts with the same contents: ``runscript.hy`` (directly under root — launched by path) and
    ``pkg/tool.hy`` (launched with ``hy -m pkg.tool``). Both expand the macro once and print the value. Returns the script launched by path."""
    _write_counting_package(root, 1)
    body = "(require pkg.macros [answer])\n(print (answer))\n"
    (root / "pkg" / "tool.hy").write_text(body)
    script = root / "runscript.hy"
    script.write_text(body)
    return script


def _hy_command(script: Path, by: str) -> str:
    """Check statement for the form that launches the script (by = "path": ``hy <path>``; "module": ``hy -m pkg.tool``)."""
    argv = ["hy", str(script)] if by == "path" else ["hy", "-m", "pkg.tool"]
    return _RUN_BY_HY_COMMAND.format(argv=argv)


def _script_pyc(root: Path, by: str) -> Path:
    """The .pyc of the _write_scripts script (by = "path" is directly under root; "module" is under pkg)."""
    cache, stem = (root, "runscript") if by == "path" else (root / "pkg", "tool")
    [pyc] = (cache / "__pycache__").glob(f"{stem}.*.pyc")
    return pyc


@pytest.mark.parametrize("by", ["path", "module"])
def test_a_script_run_by_the_hy_command_is_not_expanded_again_by_the_next_run(tmp_path: Path, by: str) -> None:
    """``hy <script>`` and ``hy -m`` do not go through import, and runpy calls the loader entry point's get_code before putting the module in ``sys.modules``.
    Hy compiles in a provisional module and removes it right after, so no record was attached, that .pyc was always treated as stale and compiled again on the next load,
    and it did not go into the store either (a gate script cost about 13 CPU seconds even on the second and later runs — same for ``hy <script>`` and ``hy -m``).
    If it compiles while the module is placed, the script also gets a .pyc with a record like an imported module, and the second run does not expand.
    When the macro changes it expands again as before (the record check works)."""
    script = _write_scripts(tmp_path)
    command = _hy_command(script, by)
    for _ in range(3):
        assert _probe_on_pypi_hy(tmp_path, "before-hy", command).strip() == "11"
        assert _expansions(tmp_path) == 1, "the second and later launches expanded the script's macro again"
    record = records.record_of(marshal.loads(_script_pyc(tmp_path, by).read_bytes()[records.PYC_HEADER_BYTES :]))
    assert record is not None, "no record was attached to the script's .pyc"
    assert [(used.module, used.name) for used in record.macros] == [("pkg.macros", "answer")]
    _edit(tmp_path / "pkg" / "macros.hy", _counting_macros(2))
    assert _probe_on_pypi_hy(tmp_path, "before-hy", command).strip() == "12", "ran with the old expansion even after the macro changed"
    assert _expansions(tmp_path) == 2


@pytest.mark.parametrize("by", ["path", "module"])
def test_a_script_run_in_a_new_tree_takes_the_stores_code_without_expanding(tmp_path: Path, by: str) -> None:
    """Launching the same script in a new work tree (same contents, different absolute path, no .pyc) uses the code added to the store when it was launched in another tree,
    and does not expand again (the store key name for ``hy <script>`` is the file name ``runscript``; for ``hy -m pkg.tool`` it is ``pkg.tool``)."""
    store = tmp_path / "store"
    first, second = tmp_path / "first", tmp_path / "second"
    for root in (first, second):
        root.mkdir()
    scripts = {root: _write_scripts(root) for root in (first, second)}
    assert _probe_on_pypi_hy(first, "before-hy", _hy_command(scripts[first], by), store).strip() == "11"
    assert _expansions(first) == 1
    assert _probe_on_pypi_hy(second, "before-hy", _hy_command(scripts[second], by), store).strip() == "11"
    assert _expansions(second) == 0


def test_a_read_outside_import_gets_its_module_only_while_it_compiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hy code compiled the way runpy calls it (make a loader and call get_code directly without placing a module) has a record of the macros used
    and goes into the store. The placed module does not remain after the read (``sys.modules`` is not polluted)."""
    import hy  # noqa: F401 — load the entry point that compiles Hy source
    from doeff_hy_bytecode_guard import _code_store as code_store

    store = tmp_path / "store"
    monkeypatch.setenv(code_store.STORE_ENV, str(store))
    package = tmp_path / "outsidepkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "macros.hy").write_text("(defmacro answer [] 11)\n")
    source = tmp_path / "outsidescript.hy"
    source.write_text("(require outsidepkg.macros [answer])\n(setv value (answer))\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    name = "outsidescript"
    try:
        loader = importlib.machinery.SourceFileLoader(name, str(source))
        code = loader.get_code(name)
        assert code is not None
        record = records.record_of(code)
        assert record is not None, "no record was attached to Hy code read outside import"
        assert [(used.module, used.name) for used in record.macros] == [("outsidepkg.macros", "answer")]
        assert name not in sys.modules, "the module placed for the read remained after it"
        assert len(_stored_entries(store, name, source, source.read_bytes())) == 1
    finally:
        for loaded in (name, "outsidepkg.macros", "outsidepkg"):
            sys.modules.pop(loaded, None)


def test_a_read_outside_import_does_not_replace_another_files_module_of_the_same_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the script's name equals the name of a module already loaded from another file (``hy json.hy``), that module is not replaced —
    even during compile, the module of the same name that the user's expansion imports stays as it was (no record is added — as before)."""
    import hy  # noqa: F401 — load the entry point that compiles Hy source
    from doeff_hy_bytecode_guard import _loader_hooks as loader_hooks

    source = tmp_path / "clashing.hy"
    source.write_text("(setv value 1)\n")
    other = types.ModuleType("clashing")
    other.__file__ = str(tmp_path / "elsewhere" / "clashing.py")
    monkeypatch.setitem(sys.modules, "clashing", other)
    loader = importlib.machinery.SourceFileLoader("clashing", str(source))
    with loader_hooks._expansion_recording(loader, str(source)):
        assert sys.modules["clashing"] is other
    assert sys.modules["clashing"] is other


#: A value that checks, without reading the table's names, whether a name was brought into the user's table by Hy's ``(require provider *)`` — the user has a function of the same name, and calling it gives
#: the macro's value if it is a macro expansion, or this answer of the function if the name was not brought in (a macro is looked up before a function of the same name).
_PLAIN_FUNCTION = "plain function"


def _write_internal_names_package(package: Path) -> None:
    """A package that checks Hy's table names and export-name table by Hy's own behavior: 2 providers (provider, which narrows public names with export, and
    plain, which does not), 2 users that require everything, and 1 user that requires a reader macro."""
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "provider.hy").write_text(
        "(export :macros [answer _visible undefined])\n"
        "(defmacro answer [] 1)\n"
        "(defmacro hidden [] 2)\n"
        "(defmacro _visible [] 3)\n"
        "(defreader twice (setv x (.parse-one-form &reader)) `(* 2 ~x))\n"
    )
    (package / "plain.hy").write_text("(defmacro answer [] 1)\n(defmacro _private [] 2)\n")
    (package / "user_all.hy").write_text(
        f"(require {package.name}.provider *)\n"
        f'(defn hidden [] "{_PLAIN_FUNCTION}")\n'
        '(setv seen {"answer" (answer) "_visible" (_visible) "hidden" (hidden)})\n'
    )
    (package / "user_plain.hy").write_text(
        f"(require {package.name}.plain *)\n"
        f'(defn _private [] "{_PLAIN_FUNCTION}")\n'
        '(setv seen {"answer" (answer) "_private" (_private)})\n'
    )
    (package / "user_reader.hy").write_text(
        f"(require {package.name}.provider :readers [twice])\n(setv value #twice 5)\n"
    )


def test_records_reads_the_macro_tables_and_the_exported_names_the_way_hy_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reading of the macro tables Hy puts in a module (ordinary macros, reader macros) and of the names ``(require provider *)`` brings in agrees, for the definition site
    (``records.macro_tables``, ``records.exported_macro_names``) on a real Hy module, with Hy's own behavior —
    the literals of the table names (``MACRO_TABLE``, ``READER_TABLE``) and of the export-name table (``EXPORT_NAMES``) are in one place in records, so if Hy renames them this goes red
    (the definition site of the names read by result_cache, macro_use, and the wrapper's tests). The brought-in side does not read
    table names, and measures with the user having a function of the same name (the macro is looked up first)."""
    import hy  # noqa: F401 — load the entry point that compiles Hy source
    from doeff_hy_bytecode_guard import _code_store as code_store

    monkeypatch.setenv(code_store.STORE_ENV, str(tmp_path / "store"))
    name = "internalnamespkg"
    _write_internal_names_package(tmp_path / name)
    monkeypatch.syspath_prepend(str(tmp_path))
    modules = ("provider", "plain", "user_all", "user_plain", "user_reader")
    try:
        loaded = {each: importlib.import_module(f"{name}.{each}") for each in modules}

        def tables(module: object) -> list[list[str]]:
            return [sorted(table) for table in records.macro_tables(vars(module))]

        def macros_entered(user: object) -> frozenset[str]:
            """Among the user's ``seen``, the names that became a macro expansion (values that are not the function's answer)."""
            return frozenset(key for key, value in vars(user)["seen"].items() if value != _PLAIN_FUNCTION)

        assert macros_entered(loaded["user_all"]) == {"answer", "_visible"}, "only the names narrowed by export are brought in"
        assert records.exported_macro_names(vars(loaded["provider"])) == macros_entered(loaded["user_all"])
        assert macros_entered(loaded["user_plain"]) == {"answer"}, "without export, names not starting with _ are brought in"
        assert records.exported_macro_names(vars(loaded["plain"])) == macros_entered(loaded["user_plain"])
        assert tables(loaded["provider"]) == [["_visible", "answer", "hidden"], ["twice"]]
        assert tables(loaded["user_all"]) == [["_visible", "answer"], []]
        assert [each for table in tables(loaded["user_reader"]) for each in table] == ["twice"]
        assert vars(loaded["user_reader"])["value"] == 10
    finally:
        for each in (*(f"{name}.{each}" for each in modules), name):
            sys.modules.pop(each, None)


#: A source containing one of each of the 3 forms where Hy assigns temporary names — match used as a value (``_hy_anon_<count>``), let (``_hy_let_<original name>_<count>``), and
#: an except binding (``_hy_exc_<original name>_<count>``).
_TEMPORARIES_SOURCE = """\
(defn f [a]
  (setv x (match a 1 "one" _ "other"))
  (let [first 1 second 2 x1 3] (+ first second x1))
  (try (g) (except [e Exception] e))
  (try (g) (except [e2 Exception] e2))
  x)
"""
#: The same source with one let added before it — the numbers of the later temporary names shift (this happens just by adding a definition).
_TEMPORARIES_SHIFTED = "(defn shifted [] (let [first 1] first))\n" + _TEMPORARIES_SOURCE


def _hy_temporaries(source: str) -> frozenset[str]:
    """Names in the tree made by compiling source with Hy that are not in the spelling of source — the temporary names Hy assigned (the head of a name is not read: even if Hy changes the head,
    the changed names are collected)."""
    import ast
    import re
    import types

    import hy
    import hy.compiler

    module = types.ModuleType("internal_names_probe")
    module.__file__ = "internal_names_probe.hy"
    tree = hy.compiler.hy_compile(
        hy.read_many(source, filename=module.__file__), module, filename=module.__file__, source=source
    )
    written = frozenset(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", source))
    named: set[str] = set()
    for node in ast.walk(tree):
        match node:
            case ast.Name(id=name) | ast.arg(arg=name) | ast.FunctionDef(name=name) | ast.ExceptHandler(name=str(name)):
                named.add(name)
    return frozenset(named) - written


def test_the_temporaries_hy_names_have_the_shape_records_folds() -> None:
    """The form Hy assigns to temporary names (``_hy_anon_<count>`` for match used as a value, ``_hy_let_<original name>_<count>`` for let, and
    ``_hy_exc_<original name>_<count>`` for except) stays the form that the definition site ``records.ANON_VAR_NAME`` reads — names with the count folded are the same even if adding a definition
    shifts the numbers (the base point of doeff-hy-check does not count the same red as a new red because of a different number). If Hy changes the name form, names that cannot be folded appear and
    this goes red (the form is in one place in records. doeff_hy._static_baseline and static_stub hold no literals)."""
    from doeff_hy._static_baseline import comparable_message
    from doeff_hy._static_stub import hidden_name

    plain = _hy_temporaries(_TEMPORARIES_SOURCE)
    shifted = _hy_temporaries(_TEMPORARIES_SHIFTED)
    assert plain != shifted, "the numbers did not shift — this test is not measuring the number shift"
    folded = {name: records.fold_anon_var_numbers(name, "#") for name in plain | shifted}
    assert {folded[name] for name in plain} == {
        "_hy_anon_#",
        "_hy_exc_e_#",
        "_hy_let_first_#",
        "_hy_let_second_#",
        "_hy_let_x1_#",
        "_hy_exc_e2_#",
    }, f"the temporary names Hy assigned are not in a foldable form (including names whose original name ends in a digit): {sorted(plain)}"
    assert {folded[name] for name in shifted} == {folded[name] for name in plain}
    assert {comparable_message(f"x is {name}") for name in plain} == {
        comparable_message(f"x is {name}") for name in shifted
    } == {f"x is {each}" for each in set(folded.values())}, "the base-point key depends on the numbers of temporary names"
    assert all(hidden_name(name) for name in plain | shifted), "the type surface does not hide the temporary names Hy assigned"


def test_the_reading_hook_builds_the_record_of_one_code_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reading hook reads the record of the same code consecutively for the check and for remembering — building the record (checking the shape of all its fields) is once per code, and
    rebuilt when switching to a different code (it was built twice each for the 2,500 modules of the declaration tool's import)."""
    from doeff_hy_bytecode_guard import _loader_hooks as loader_hooks

    record = records.MacroRecord(_locked_hy_version(), (), (), (), (), ())
    first = records.with_record(compile("value = 1\n", "first.hy", "exec"), record)
    second = records.with_record(compile("value = 2\n", "second.hy", "exec"), record)
    built: list[types.CodeType] = []
    record_of = records.record_of

    def counting(code: types.CodeType) -> records.MacroRecord | None:
        built.append(code)
        return record_of(code)

    monkeypatch.setattr(records, "record_of", counting)
    monkeypatch.setattr(loader_hooks, "_last_record", {})
    assert loader_hooks._record_of(first) == record
    assert loader_hooks._remember_record("first.hy", first) == record
    assert built == [first]
    assert loader_hooks._record_of(second) == record
    assert built == [first, second]
