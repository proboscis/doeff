"""Counterexamples for the canonicalization that renumbers Hy gensym names in module order (doeff_hy_bytecode_guard._records.canonical_gensyms, agora-redesign
#3667).

Hy's ``hy.gensym`` counter is one per process and advances on every gensym call. So even for the same source, the local variable names
(``_hy_gensym_bound_1890`` vs ``_3015``) changed depending on whether another module was compiled first, whether the .pyc of a required module existed, or
whether another thread was compiling at the same time, and that made the .pyc, the shared code store, and the packed Program fingerprint (doeff-cluster program_codec) unstable. The compile entry point (the wrapper's
source_to_code) renames the gensym names in the compiled code, so every compile path yields the same code.

The sample modules are written to tmp_path. The cross-process checks share the .pyc store (PYTHONPYCACHEPREFIX) within this file (only the first child
compiles doeff) and turn off the shared code store (DOEFF_HY_CODE_STORE=off, since pulling from the store means no compile happens).
"""

from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import json
import subprocess
import sys
import threading
import types
from collections.abc import Iterator
from pathlib import Path
from types import CodeType

import pytest
from doeff_hy_bytecode_guard import _code_store as code_store
from doeff_hy_bytecode_guard import _records as records
from doeff_hy_bytecode_guard._loader_hooks import source_to_code_as_import

#: Marker of the pre-fix record (a record of code whose gensym names were not canonicalized).
PREVIOUS_RECORD_TAG = "doeff-hy/macro-dependencies/1"

#: Name of the sample package (used both by the child process and by this process's import).
PACKAGE = "gsprobe"

#: Sample that passes through every doeff-hy macro that uses gensym (<- binds, absent-as let, on-raise handler, defrecord :check, defk contract
#: check, validate check, defhandler session val - lazy temporary names). The job can run without going through validate (validate needs a Traverse handler).
EVERYTHING = """\
(require doeff-hy.macros [defk <- absent-as on-raise validate check])
(require doeff-hy.handle [defhandler])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_core_effects [Ask])

(defclass [(dataclass :frozen True)] Fetch [EffectBase]
  #^ str source)

(defclass [(dataclass :frozen True)] Conflict []
  "Loses on version (type of the on-raise handler)."
  #^ str detail)

(defrecord Positive
  "positive number"
  {:tags {:context "gensym-probe" :role "type"}
   :check [(> value 0)]}
  (#^ int value))

(defk plus-one [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "gensym-probe" :role "judgment"}}
  "Add 1 (partner of the binds)."
  (+ n 1))

(defk job [n]
  {:pre [(: n int) (check > n 0 :reason "positive")] :post [(: % int)] :tags {:context "gensym-probe" :role "entry"}}
  "A job that goes through the gensym-using macros."
  (<- a (plus-one n))
  (<- b (absent-as 0 (plus-one a)))
  (<- c (on-raise (plus-one b) (Conflict d) 0))
  (+ a b c (. (Positive :value 1) value)))

(defk validated [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "gensym-probe" :role "entry"}}
  "Passes the validate check (only compile is observed)."
  (! (validate (check = n n :reason "same")))
  n)

(defhandler fetch-handler
  (session val client (+ "client:" (! (Ask "endpoint"))))
  (Fetch [source]
    (resume (+ client ":" source))))
"""

#: Another module that is compiled first to advance the counter (both gensym and the session val temporary names).
OTHER = """\
(require doeff-hy.macros [defk <-])
(require doeff-hy.handle [defhandler])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_core_effects [Ask])

(defclass [(dataclass :frozen True)] Other [EffectBase]
  #^ str source)

(defk step [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "gensym-probe" :role "judgment"}}
  "Add 1."
  (+ n 1))

(defk other-job [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "gensym-probe" :role "entry"}}
  "Three binds."
  (<- a (step n))
  (<- b (step a))
  (<- c (step b))
  c)

(defhandler other-handler
  (session val connection (+ "connection:" (! (Ask "endpoint"))))
  (Other [source]
    (resume (+ connection ":" source))))
"""

#: Provider of the macro to be required - it also uses gensym in its own compile (compiled in the middle of the requiring side's compile).
MYMACROS = """\
(require doeff-hy.macros [defk <-])

(defk helper-step [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "gensym-probe" :role "judgment"}}
  "Add 1."
  (+ n 1))

(defk helper [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "gensym-probe" :role "judgment"}}
  "One bind."
  (<- a (helper-step n))
  a)

(defmacro twice [x]
  (setv g (hy.gensym "twice"))
  `(do (setv ~g ~x) (+ ~g ~g)))
"""

#: Module that requires mymacros in the middle of its compile.
NESTED = f"""\
(require {PACKAGE}.mymacros [twice])
(require doeff-hy.macros [defk <-])
(import {PACKAGE}.mymacros [helper])

(defk job [n]
  {{:pre [(: n int)] :post [(: % int)] :tags {{:context "gensym-probe" :role "entry"}}}}
  "A job that uses the gensym of the required macro."
  (<- a (helper n))
  (twice a))
"""

#: Child process: adds the sample root to sys.path, imports the named modules in order, and returns the generated names of the target module's code (what was compiled at import or
#: read from .pyc), the job's result, and the names of sample modules whose .pyc existed before the import.
_CHILD = f"""\
import importlib, importlib.machinery, importlib.util, json, os, re, sys
root, first, target = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, root)
import doeff_hy
from doeff import run
from doeff_hy_bytecode_guard import _records as records

package = os.path.join(root, "{PACKAGE}")
had_pyc = sorted(
    "{PACKAGE}." + name[: -len(".hy")]
    for name in os.listdir(package)
    if name.endswith(".hy") and os.path.exists(importlib.util.cache_from_source(os.path.join(package, name)))
)
codes = {{}}
previous_get_code = importlib.machinery.SourceFileLoader.get_code


def get_code(self, fullname):
    code = previous_get_code(self, fullname)
    codes[fullname] = code
    return code


importlib.machinery.SourceFileLoader.get_code = get_code
for name in filter(None, first.split(",")):
    importlib.import_module(name)
module = importlib.import_module(target)


def texts(code):
    yield from (*code.co_varnames, *code.co_cellvars, *code.co_freevars, *code.co_names)
    for constant in code.co_consts:
        if isinstance(constant, type(code)):
            yield from texts(constant)
        elif isinstance(constant, str):
            yield constant


generated = sorted({{
    text for text in texts(codes[target])
    if records.gensym_renames([text]) or re.fullmatch(r"_lazy_.+_(cached|val)_[0-9]+", text)
}} | set(records.unread_gensym_texts(codes[target])))
value = run(module.job(1)) if hasattr(module, "job") else None
print(json.dumps({{"names": generated, "value": value, "had_pyc": had_pyc}}))
"""


@pytest.fixture(scope="module")
def pycache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The .pyc store shared by this file's child processes (only the first child compiles doeff and Hy)."""
    return tmp_path_factory.mktemp("gensym-pycache")


def _probe(root: Path) -> Path:
    """Place the sample package under root and return root (placing a package with the same content under a different root means the target .pyc is not shared)."""
    package = root / PACKAGE
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    for name, source in (
        ("everything", EVERYTHING),
        ("other", OTHER),
        ("mymacros", MYMACROS),
        ("nested", NESTED),
    ):
        (package / f"{name}.hy").write_text(source)
    return root


def _binds_module(binds: int) -> str:
    """Source of a module with one defk that lines up `binds` binds (same binds gives same source) - material for the side-by-side compile check."""
    lines = [f"  (<- v{index} (step {index}))" for index in range(binds)]
    return "\n".join(
        [
            "(require doeff-hy.macros [defk <-])",
            "(defk step [n]",
            '  {:pre [(: n int)] :post [(: % int)] :tags {:context "gensym-probe" :role "judgment"}}',
            '  "Add 1."',
            "  (+ n 1))",
            "(defk many [n]",
            '  {:pre [(: n int)] :post [(: % int)] :tags {:context "gensym-probe" :role "entry"}}',
            '  "Line up the binds."',
            *lines,
            "  n)",
            "",
        ]
    )


def _child(root: Path, first: list[str], target: str, pycache: Path) -> dict[str, object]:
    """Run a separate process with this venv's python, import `first` in order, then import `target`, and return the result (generated names, job result,
    compiled modules). .pyc is written (to create the presence or absence of the required module's .pyc) - the store is pycache."""
    # The child inherits this process's environment - only this suite's fixed PYTHONDONTWRITEBYTECODE=1 is removed (`env -u`), and the two store settings are added.
    done = subprocess.run(
        [
            "env",
            "-u",
            "PYTHONDONTWRITEBYTECODE",
            f"PYTHONPYCACHEPREFIX={pycache}",
            f"{code_store.STORE_ENV}=off",
            sys.executable,
            "-c",
            _CHILD,
            str(root),
            ",".join(first),
            target,
        ],
        capture_output=True,
        text=True,
        cwd=root,
        timeout=50,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    answer = json.loads(done.stdout.splitlines()[-1])
    assert isinstance(answer, dict)
    return answer


def _texts(code: CodeType) -> Iterator[str]:
    """Table of names in the code tree and string constants (where generated names appear)."""
    yield from (*code.co_varnames, *code.co_cellvars, *code.co_freevars, *code.co_names)
    for constant in code.co_consts:
        if isinstance(constant, CodeType):
            yield from _texts(constant)
        elif isinstance(constant, str):
            yield constant


def _gensym_names(code: CodeType) -> list[str]:
    """List the gensym names in the code tree (for names embedded in a renamed binding, the embedded gensym name) - the name shape is read by the canonicalization's
    own definitions (records GENSYM_NAME, RENAMED_BINDING) and is not copied into this file (if Hy changes the shape, only records needs fixing)."""
    return sorted(records.gensym_renames(_texts(code)))


def _numbers(code: CodeType) -> list[int]:
    """Numbers of the gensym names in the code tree (smallest first - the second group of records.GENSYM_NAME is the counter)."""
    return sorted(
        int(match.group(2))
        for name in _gensym_names(code)
        if (match := records.GENSYM_NAME.fullmatch(name))
    )


def _compile(root: Path, module: str) -> CodeType:
    """Compile the sample module through the same entry point as the tool that precompiles bytecode (compile inside the module's location)."""
    path = root / PACKAGE / f"{module}.hy"
    loader = importlib.machinery.SourceFileLoader(f"{PACKAGE}.{module}", str(path))
    return source_to_code_as_import(loader, path.read_bytes(), str(path))


@pytest.fixture
def probe_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Root of the sample package importable in this process (removes the sample modules from sys.modules when finished)."""
    root = _probe(tmp_path / "probe")
    monkeypatch.syspath_prepend(str(root))
    yield root
    for name in [name for name in sys.modules if name == PACKAGE or name.startswith(f"{PACKAGE}.")]:
        del sys.modules[name]


def test_a_module_gets_the_same_names_whether_another_module_was_compiled_first(
    tmp_path: Path, pycache: Path
) -> None:
    """Failure cases (a)(d): compile the same module in "a process that compiled another module first" and in "a standalone process", and the generated names
    (including the name embedded in the absent-as let and the defhandler session val temporary name) are the same. Before the fix they continued the counter, so the names differed."""
    after_other = _child(
        _probe(tmp_path / "after"), [f"{PACKAGE}.other"], f"{PACKAGE}.everything", pycache
    )
    alone = _child(_probe(tmp_path / "alone"), [], f"{PACKAGE}.everything", pycache)
    assert after_other["had_pyc"] == alone["had_pyc"] == [], "the sample .pyc already existed (the current compile is not being measured)"
    assert after_other["names"] == alone["names"]
    names = alone["names"]
    assert isinstance(names, list)
    assert any(
        records.RENAMED_BINDING.fullmatch(name) and "absent_token" in name for name in names
    ), names
    assert any(records.GENSYM_NAME.fullmatch(name) and "lazy_client" in name for name in names), (
        names
    )
    assert alone["value"] == after_other["value"] == 10


def test_a_module_gets_the_same_names_whether_the_required_macro_module_has_bytecode_or_not(
    tmp_path: Path, pycache: Path
) -> None:
    """Failure case (b): the requiring side (nested) has the same generated names in a process where the .pyc of the required module (mymacros) exists and one where it does not.
    Before the fix, compiling the required module advanced the counter in the middle of the requiring side's compile, and the names shifted."""
    warm_root = _probe(tmp_path / "warm")
    _child(warm_root, [], f"{PACKAGE}.mymacros", pycache)
    warm = _child(warm_root, [], f"{PACKAGE}.nested", pycache)
    cold = _child(_probe(tmp_path / "cold"), [], f"{PACKAGE}.nested", pycache)
    assert warm["had_pyc"] == [f"{PACKAGE}.mymacros"], warm["had_pyc"]
    assert cold["had_pyc"] == [], cold["had_pyc"]
    assert warm["names"] == cold["names"]
    assert warm["value"] == cold["value"] == 4


def test_compiles_on_two_threads_at_once_keep_every_name_apart_and_the_same(probe_root: Path) -> None:
    """Failure case (c): compile two modules of BINDS binds each at the same time in two threads (ROUNDS times). One thread first
    compiles a prelude module of a different size each round, so the overlap of the two compiles shifts from round to round. The number of
    bind names in a module equals the number of binds (no collisions), and the names are the same every time. Before the fix, the gensyms of the two compiles shared one counter
    and the names changed from round to round."""
    rounds, binds = 4, 80
    for turn in range(rounds):
        for side in ("a", "b"):
            (probe_root / PACKAGE / f"race_{turn}_{side}.hy").write_text(_binds_module(binds))
        (probe_root / PACKAGE / f"lead_{turn}.hy").write_text(_binds_module(15 * turn))
    modules = [f"race_{turn}_{side}" for turn in range(rounds) for side in ("a", "b")]
    results: dict[str, list[str]] = {}

    def compile_on_thread(modules_in_order: list[str], start: threading.Barrier) -> None:
        """Compile modules_in_order in order after aligning at start, and keep the generated names of the last module (to make the two compiles overlap)."""
        start.wait()
        for module in modules_in_order:
            results[module] = _gensym_names(_compile(probe_root, module))

    for turn in range(rounds):
        start = threading.Barrier(2)
        threads = [
            threading.Thread(target=compile_on_thread, args=([f"race_{turn}_a"], start)),
            threading.Thread(target=compile_on_thread, args=([f"lead_{turn}", f"race_{turn}_b"], start)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    assert set(modules) <= set(results)
    first = results[modules[0]]
    for module in modules:
        bound = [
            name
            for name in results[module]
            if (match := records.GENSYM_NAME.fullmatch(name)) and "bound" in match.group(1)
        ]
        assert len(bound) == binds, (module, len(bound))
        assert results[module] == first, module


def test_a_pyc_whose_record_has_the_previous_tag_is_compiled_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure case (e): a .pyc with the pre-fix marker (code whose gensym names were not canonicalized) is not used even if source and timestamp match; it is compiled
    again. With the old marker kept, the contents of the old .pyc would continue to be used."""
    import hy

    monkeypatch.setenv(code_store.STORE_ENV, "off")
    # This suite runs with PYTHONDONTWRITEBYTECODE=1 - the .pyc read and write paths are opened only within this check (store is tmp_path).
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    monkeypatch.setattr(sys, "pycache_prefix", str(tmp_path / "pyc"))
    package = tmp_path / "oldtagpkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    source = package / "mod.hy"
    source.write_text('(setv value "new")\n')
    stale = compile("value = 'old'\n", str(source), "exec")
    stale = stale.replace(co_consts=(*stale.co_consts, (PREVIOUS_RECORD_TAG, hy.__version__, ())))
    status = source.stat()
    pyc = Path(importlib.util.cache_from_source(str(source)))
    pyc.parent.mkdir(parents=True)
    pyc.write_bytes(
        records.pyc_bytes(
            stale,
            source=source.read_bytes(),
            mtime=int(status.st_mtime),
            size=status.st_size,
            previous_header=None,
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        assert importlib.import_module("oldtagpkg.mod").value == "new"
    finally:
        for name in ("oldtagpkg.mod", "oldtagpkg"):
            sys.modules.pop(name, None)


def test_the_image_build_compile_and_the_prebuild_compile_number_gensyms_from_one(
    probe_root: Path,
) -> None:
    """The image build (agora-controllers deploy/bytecode.py - calls ``SourceFileLoader(...).source_to_code`` directly) and the compile of the tool that precompiles
    bytecode (source_to_code_as_import) also go through the same entry point, and the gensym numbers run from 1 without gaps (this process's
    counter is already large). Before the fix, both continued from the process counter."""
    path = probe_root / PACKAGE / "everything.hy"
    direct = importlib.machinery.SourceFileLoader(f"{PACKAGE}.everything", str(path)).source_to_code(
        path.read_bytes(), str(path)
    )
    prebuilt = _compile(probe_root, "everything")
    assert _numbers(direct) == list(range(1, len(_numbers(direct)) + 1)), _gensym_names(direct)
    assert _gensym_names(direct) == _gensym_names(prebuilt)


def test_the_canonical_module_still_runs(probe_root: Path) -> None:
    """The module of canonicalized code works as before (names table and constants were only replaced one-to-one - the job result through binds, absent-as, on-raise, contract check,
    defrecord :check)."""
    from doeff import run

    module = importlib.import_module(f"{PACKAGE}.everything")
    assert run(module.job(1)) == 10


def test_hy_gensym_names_and_hy_renamed_bindings_have_the_shapes_the_canonicalisation_reads() -> None:
    """The gensym name shape of Hy (``_hy_gensym_<mangled base>_<counter>``) and the let-renamed binding shape (``_hy_let_<original name>_<number>``) remain
    what the canonicalization reads. If Hy is upgraded and the shape changes, the canonicalization cannot read names and the instability silently returns, so fail here."""
    import hy
    from hy.compiler import hy_compile
    from hy.models import Expression, Integer, List, Symbol

    for base, mangled in (("bound", "bound"), ("el-begin", "el_begin"), ("", "")):
        name = str(hy.gensym(base))
        match = records.GENSYM_NAME.fullmatch(name)
        assert match is not None and mangled in match.group(1), name
    token = hy.gensym("token")
    tree = Expression(
        [
            Symbol("defn"),
            Symbol("f"),
            List([]),
            Expression([Symbol("let"), List([token, Integer(1)]), token]),
        ]
    )
    compiled = hy_compile(tree, types.ModuleType("gsshape"))
    assert isinstance(compiled, ast.Module)  # without get_expr the result is only a module
    renamed = [
        node.id
        for node in ast.walk(compiled)
        if isinstance(node, ast.Name) and str(token) in node.id and node.id != str(token)
    ]
    assert renamed, ast.dump(compiled)
    for name in renamed:
        match = records.RENAMED_BINDING.fullmatch(name)
        assert match is not None and match.group(2) == str(token), name


def test_no_gensym_name_is_left_where_the_canonicalisation_cannot_read_it(probe_root: Path) -> None:
    """After compiling the module that passes through every gensym-using doeff-hy macro, there are no names or string constants that contain a gensym name but that the canonicalization cannot read
    (if a name embedded in a statement appears, its number would change with compile order). Also confirm that unreadable ones can be told apart."""
    import hy

    assert records.unread_gensym_texts(_compile(probe_root, "everything")) == ()
    message = f"bad {hy.gensym('x')} here"
    leftover = compile(f"message = {message!r}\n", "<leftover>", "exec")
    assert records.unread_gensym_texts(leftover) == (message,)
