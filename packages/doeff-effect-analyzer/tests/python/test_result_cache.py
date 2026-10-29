"""An analysis answer is reused from disk only while every module file it depends on has the same content.

The answer of a closure analysis depends on the sources it reads and on the runtime values importing
them made (agora-redesign #1645). It is keyed on the files of the modules the analysis read and the
modules their values were built from — not on every module the process had loaded (ki-30d28550680c:
a test session loads hundreds of unrelated test files, and one change among them made every closure
test analyze again). One changed byte in a module of the key must make the next call analyze again;
a change in a module outside it must not. The key is the files' content, not their path
(agora-redesign #3777): another checkout with the same content reads the stored answer, and
checkouts with other contents keep their answers side by side.
"""

import contextlib
import functools
import importlib
import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from doeff_effect_analyzer import _program_effects as pe
from doeff_effect_analyzer import _result_cache as rc


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "trees"
    monkeypatch.setenv("DOEFF_EFFECT_ANALYZER_CACHE", str(directory))
    monkeypatch.delenv("DOEFF_EFFECT_ANALYZER_RESULT_CACHE", raising=False)
    return directory


@pytest.fixture
def material(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A module the analysis runs under (loaded in sys.modules, as a job's module would be)."""
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    module_file = source_dir / "result_cache_material.py"
    module_file.write_text("ANSWER = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(source_dir))
    importlib.import_module("result_cache_material")
    yield module_file
    sys.modules.pop("result_cache_material", None)


class Counting:
    """An analysis that counts how often it really runs. It reads the source of the module ``reads``
    when that is loaded, as the closure analysis reads a job's module."""

    def __init__(self, reads: str = "result_cache_material") -> None:
        self.reads = reads
        self.runs = 0

    def __call__(self) -> tuple[str, ...]:
        self.runs += 1
        module = sys.modules.get(self.reads)
        if module is not None:
            pe.module_ast(module)
        return ("gap ← somewhere",)


def reading(name: str, answer: tuple[str, ...]) -> Callable[[], tuple[str, ...]]:
    """An analysis that reads the source of the loaded module ``name`` and answers ``answer``."""

    def analysis() -> tuple[str, ...]:
        pe.module_ast(sys.modules[name])
        return answer

    return analysis


IDENTITY = ("test.analysis", "module:job", "module:foundation")


def test_the_second_call_reads_the_stored_answer(cache_dir: Path, material: Path) -> None:
    analysis = Counting()
    first = rc.cached_result(IDENTITY, analysis)
    second = rc.cached_result(IDENTITY, analysis)
    assert first == second == ("gap ← somewhere",)
    assert analysis.runs == 1


def test_one_changed_byte_of_a_loaded_module_analyzes_again(cache_dir: Path, material: Path) -> None:
    analysis = Counting()
    rc.cached_result(IDENTITY, analysis)
    material.write_text("ANSWER = 2\n", encoding="utf-8")  # the same size: one byte differs
    rc.cached_result(IDENTITY, analysis)
    assert analysis.runs == 2
    rc.cached_result(IDENTITY, analysis)
    assert analysis.runs == 2


def test_a_file_rewritten_with_its_old_modification_time_is_read_again(cache_dir: Path, material: Path) -> None:
    """The stat that spares reading a file includes its change time, which a write always moves —
    restoring the modification time does not make a changed file look unchanged."""
    analysis = Counting()
    rc.cached_result(IDENTITY, analysis)
    before = material.stat()
    material.write_text("ANSWER = 3\n", encoding="utf-8")
    os.utime(material, ns=(before.st_atime_ns, before.st_mtime_ns))
    rc.cached_result(IDENTITY, analysis)
    assert analysis.runs == 2


def checkout_with(tmp_path: Path, label: str, name: str, text: str) -> Path:
    """A directory standing for one checkout, holding the module ``name`` with ``text``."""
    directory = tmp_path / f"checkout_{label}"
    directory.mkdir()
    (directory / f"{name}.py").write_text(text, encoding="utf-8")
    return directory


def load_from(directory: Path, name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Load ``name`` from ``directory`` alone, as a process started in that checkout would."""
    sys.modules.pop(name, None)
    for entry in [entry for entry in sys.path if entry.startswith(str(directory.parent))]:
        sys.path.remove(entry)
    monkeypatch.syspath_prepend(str(directory))
    importlib.import_module(name)


def no_reading(status: rc._Stat) -> str | None:
    """Stands in for reading a file's content where no file may be read."""
    raise AssertionError(f"read {status.path}")


def test_the_same_content_in_another_checkout_reads_the_stored_answer(
    cache_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new worktree at the same commit reads the answer another worktree stored (agora-redesign
    #3777 — the key named each file's path, so every new worktree ran the analysis in full), and
    stores it again with its own files' stat, so its next call reads none of them."""
    name = "result_cache_same_content"
    first = checkout_with(tmp_path, "a", name, "CHECKOUT = 'same'\n")
    second = checkout_with(tmp_path, "b", name, "CHECKOUT = 'same'\n")
    try:
        load_from(first, name, monkeypatch)
        assert rc.cached_result(IDENTITY, reading(name, ("from a",))) == ("from a",)
        load_from(second, name, monkeypatch)
        assert rc.cached_result(IDENTITY, reading(name, ("from b",))) == ("from a",)
        monkeypatch.setattr(rc, "_digest", no_reading)
        assert rc.cached_result(IDENTITY, reading(name, ("again",))) == ("from a",)
    finally:
        sys.modules.pop(name, None)


def test_checkouts_with_other_contents_keep_their_answers_side_by_side(
    cache_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two worktrees at different commits running the same analysis do not overwrite each other's
    answer (agora-redesign #3777 — an identity had one answer, and the closure test of the screen
    job ran in full whenever another worktree had run it last)."""
    name = "result_cache_side_by_side"
    first = checkout_with(tmp_path, "a", name, "CHECKOUT = 'a'\n")
    second = checkout_with(tmp_path, "b", name, "CHECKOUT = 'b'\n")
    try:
        load_from(first, name, monkeypatch)
        assert rc.cached_result(IDENTITY, reading(name, ("from a",))) == ("from a",)
        load_from(second, name, monkeypatch)
        assert rc.cached_result(IDENTITY, reading(name, ("from b",))) == ("from b",)
        load_from(first, name, monkeypatch)
        assert rc.cached_result(IDENTITY, reading(name, ("again in a",))) == ("from a",)
        load_from(second, name, monkeypatch)
        assert rc.cached_result(IDENTITY, reading(name, ("again in b",))) == ("from b",)
    finally:
        sys.modules.pop(name, None)


def test_a_module_not_loaded_here_is_compared_by_the_file_the_import_would_find(
    cache_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A module loaded where the answer was computed but not in this process is compared by the
    file the import system would load it from, found without importing it or its package."""
    package = tmp_path / "src" / "result_cache_package"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    submodule = package / "inner.py"
    submodule.write_text("ANSWER = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(package.parent))
    importlib.import_module("result_cache_package.inner")
    try:
        analysis = Counting("result_cache_package.inner")
        rc.cached_result(IDENTITY, analysis)
        sys.modules.pop("result_cache_package.inner")
        sys.modules.pop("result_cache_package")
        rc.cached_result(IDENTITY, analysis)
        assert analysis.runs == 1
        assert "result_cache_package" not in sys.modules
        submodule.write_text("ANSWER = 2\n", encoding="utf-8")
        rc.cached_result(IDENTITY, analysis)
        assert analysis.runs == 2
        assert "result_cache_package" not in sys.modules
    finally:
        sys.modules.pop("result_cache_package.inner", None)
        sys.modules.pop("result_cache_package", None)


def test_a_module_under_namespace_packages_not_loaded_here_does_not_raise(
    cache_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A module under two levels of namespace packages (no ``__init__``) loaded where the answer was
    computed but not in this process: the import system's namespace path reads the parent from
    ``sys.modules`` and raises KeyError when it is not loaded. The lookup must answer "not found
    here" (analyze again) instead of letting the KeyError out of the analysis (agora-redesign
    — the agora-controllers closure tests failed on ``KeyError: 'controllers.artifact_store'``)."""
    inner = tmp_path / "src" / "result_cache_ns" / "middle" / "inner.py"
    inner.parent.mkdir(parents=True)
    inner.write_text("ANSWER = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path / "src"))
    names = ("result_cache_ns.middle.inner", "result_cache_ns.middle", "result_cache_ns")
    importlib.import_module(names[0])
    try:
        analysis = Counting(names[0])
        first = rc.cached_result(IDENTITY, analysis)
        for name in names:
            sys.modules.pop(name)
        assert rc.cached_result(IDENTITY, analysis) == first
        assert analysis.runs == 2
        assert "result_cache_ns" not in sys.modules
    finally:
        for name in names:
            sys.modules.pop(name, None)


def test_an_identity_keeps_the_answers_used_most_recently(
    cache_dir: Path, material: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rc, "_KEPT", 2)
    analysis = Counting()
    for text in ("ANSWER = 1\n", "ANSWER = 2\n", "ANSWER = 3\n"):
        material.write_text(text, encoding="utf-8")
        rc.cached_result(IDENTITY, analysis)
    assert analysis.runs == 3
    assert len(list(cache_dir.glob("results/*/*.pickle"))) == 2
    material.write_text("ANSWER = 2\n", encoding="utf-8")
    rc.cached_result(IDENTITY, analysis)
    assert analysis.runs == 3
    material.write_text("ANSWER = 1\n", encoding="utf-8")
    rc.cached_result(IDENTITY, analysis)
    assert analysis.runs == 4


def test_an_answer_from_another_checkout_is_not_reused(
    cache_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two checkouts of one repo share the machine's cache and name their modules alike; an answer
    computed while one checkout's module was loaded must not answer a process that loaded the same
    module from the other checkout, although the first checkout's file is still unchanged
    (agora-redesign #1864 — a closure check counted a module only the other checkout had)."""
    name = "result_cache_checkout"
    checkouts = {}
    for label in ("a", "b"):
        directory = tmp_path / f"checkout_{label}"
        directory.mkdir()
        (directory / f"{name}.py").write_text(f"CHECKOUT = {label!r}\n", encoding="utf-8")
        checkouts[label] = directory
    try:
        monkeypatch.syspath_prepend(str(checkouts["a"]))
        importlib.import_module(name)
        assert rc.cached_result(IDENTITY, reading(name, ("from a",))) == ("from a",)
        sys.modules.pop(name)
        sys.path.remove(str(checkouts["a"]))
        monkeypatch.syspath_prepend(str(checkouts["b"]))
        importlib.import_module(name)
        assert rc.cached_result(IDENTITY, reading(name, ("from b",))) == ("from b",)
        # The same checkout still reads its stored answer.
        assert rc.cached_result(IDENTITY, reading(name, ("again",))) == ("from b",)
    finally:
        sys.modules.pop(name, None)


def test_the_flag_turns_the_answer_cache_off(
    cache_dir: Path, material: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOEFF_EFFECT_ANALYZER_RESULT_CACHE", "off")
    analysis = Counting()
    rc.cached_result(IDENTITY, analysis)
    rc.cached_result(IDENTITY, analysis)
    assert analysis.runs == 2
    assert not (cache_dir / "results").exists()


def test_arguments_without_a_name_are_not_cached(cache_dir: Path, material: Path) -> None:
    analysis = Counting()
    rc.cached_result(None, analysis)
    rc.cached_result(None, analysis)
    assert analysis.runs == 2


def test_only_a_miss_is_observed(cache_dir: Path, material: Path) -> None:
    """A test budget subtracts a full analysis like an uncached expansion — only when it ran."""
    entered: list[str] = []

    @contextlib.contextmanager
    def observer() -> Iterator[None]:
        entered.append("miss")
        yield

    stop = pe.observe_expansions(observer)
    try:
        rc.cached_result(IDENTITY, Counting())
        rc.cached_result(IDENTITY, Counting())
    finally:
        stop()
    assert entered == ["miss"]


def module_level_job() -> None:
    """A job written at the top of its module (found again by its name)."""


def test_only_objects_found_again_by_their_name_are_named() -> None:
    def nested() -> None:
        """A def inside a function (no module attribute leads to it)."""

    assert rc.importable_name(module_level_job) == f"{__name__}:module_level_job"
    assert rc.importable_name(nested) is None
    assert rc.importable_name(lambda: None) is None
    assert rc.importable_name(functools.partial(module_level_job)) is None


# --- The key is the modules the answer depends on (ki-30d28550680c) ---------------------------------
# A test session loads hundreds of test files the closure analysis never reads; the key named every
# loaded module, so a change in any of them (and ``conftest`` / ``__mp_main__``, named alike in every
# session) made the next session analyze in full (20-60 s per closure test). The key is the modules
# whose source the analysis read and the modules their values were built from.


@pytest.fixture
def modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A directory on the import path whose modules are unloaded after the test."""
    directory = tmp_path / "modules"
    directory.mkdir()
    monkeypatch.syspath_prepend(str(directory))
    yield directory
    for name, module in tuple(sys.modules.items()):
        if str(module.__dict__.get("__file__") or "").startswith(str(directory)):
            sys.modules.pop(name, None)


def write(directory: Path, name: str, text: str) -> Path:
    """The module ``name`` (dotted: a package's submodule) under ``directory`` with ``text``."""
    path = directory.joinpath(*name.split(".")).with_suffix(".py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def unrelated_name(stem: str) -> str:
    """A module name no code of this test module names (a constant naming it would tie the test
    module — the analysis' own code here — to it)."""
    return "_".join(("result_key", stem))


def test_a_changed_module_the_analysis_does_not_depend_on_reuses_the_answer(
    cache_dir: Path, modules: Path
) -> None:
    """A test file loaded in the same session that the analysis neither reads nor builds a value
    from does not take part in the key: changing it reuses the answer."""
    write(modules, "result_key_job", "def job():\n    return 1\n")
    other = write(modules, unrelated_name("other_test"), "def test_something():\n    assert True\n")
    importlib.import_module("result_key_job")
    importlib.import_module(unrelated_name("other_test"))
    analysis = Counting("result_key_job")
    rc.cached_result(IDENTITY, analysis)
    other.write_text("def test_something():\n    assert 1 == 1\n", encoding="utf-8")
    rc.cached_result(IDENTITY, analysis)
    assert analysis.runs == 1


def test_a_session_entry_named_alike_from_another_file_reuses_the_answer(
    cache_dir: Path, modules: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``conftest`` and ``__mp_main__`` are loaded by every session from other files (another
    rootdir, another runner); the analysis does not depend on them, so another one reuses the answer."""
    entry = unrelated_name("conftest")
    first = checkout_with(modules.parent, "a", entry, "FIXTURE = 'a'\n")
    second = checkout_with(modules.parent, "b", entry, "FIXTURE = 'b'\n")
    write(modules, "result_key_job", "def job():\n    return 1\n")
    importlib.import_module("result_key_job")
    try:
        load_from(first, entry, monkeypatch)
        analysis = Counting("result_key_job")
        rc.cached_result(IDENTITY, analysis)
        load_from(second, entry, monkeypatch)
        rc.cached_result(IDENTITY, analysis)
        assert analysis.runs == 1
    finally:
        sys.modules.pop(entry, None)


def test_a_changed_module_a_read_module_built_a_value_from_analyzes_again(
    cache_dir: Path, modules: Path
) -> None:
    """The analysis reads the job's module only, but a value there was made by another module at
    import time (a handler from a factory): that module is in the key."""
    dep = write(
        modules,
        "result_key_dep",
        "def make():\n    def handler():\n        return 1\n    return handler\n",
    )
    write(modules, "result_key_job", "from result_key_dep import make\nHANDLER = make()\n")
    importlib.import_module("result_key_job")
    analysis = Counting("result_key_job")
    rc.cached_result(IDENTITY, analysis)
    dep.write_text(
        "def make():\n    def handler():\n        return 2\n    return handler\n", encoding="utf-8"
    )
    rc.cached_result(IDENTITY, analysis)
    assert analysis.runs == 2


def test_a_module_a_read_function_imports_when_called_is_in_the_key(
    cache_dir: Path, modules: Path
) -> None:
    """A function of a read module imports another module in its body (the analysis follows such
    imports): that module is in the key, though nothing binds it at import time."""
    lazy = write(modules, "result_key_lazy", "VALUE = 1\n")
    write(
        modules,
        "result_key_job",
        "def job():\n    import result_key_lazy\n    return result_key_lazy.VALUE\n",
    )
    importlib.import_module("result_key_job")
    importlib.import_module("result_key_lazy")
    analysis = Counting("result_key_job")
    rc.cached_result(IDENTITY, analysis)
    lazy.write_text("VALUE = 2\n", encoding="utf-8")
    rc.cached_result(IDENTITY, analysis)
    assert analysis.runs == 2


def test_a_package_submodule_is_in_the_key_only_when_a_read_module_uses_it(
    cache_dir: Path, modules: Path
) -> None:
    """``import pkg.inner`` binds ``pkg``; every loaded submodule hangs on ``pkg``, but only the one
    the read module uses is in the key — a sibling loaded by other tests is not."""
    sibling_name = ".".join(("result_key_pkg", unrelated_name("sibling")))
    write(modules, "result_key_pkg.__init__", "")
    inner = write(modules, "result_key_pkg.inner", "VALUE = 1\n")
    sibling = write(modules, sibling_name, "VALUE = 1\n")
    write(
        modules,
        "result_key_job",
        "import result_key_pkg.inner\n\ndef job():\n    return result_key_pkg.inner.VALUE\n",
    )
    importlib.import_module("result_key_job")
    importlib.import_module(sibling_name)
    analysis = Counting("result_key_job")
    rc.cached_result(IDENTITY, analysis)
    sibling.write_text("VALUE = 2\n", encoding="utf-8")
    rc.cached_result(IDENTITY, analysis)
    assert analysis.runs == 1
    inner.write_text("VALUE = 2\n", encoding="utf-8")
    rc.cached_result(IDENTITY, analysis)
    assert analysis.runs == 2


def test_the_module_of_the_analysis_is_in_the_key(cache_dir: Path, modules: Path) -> None:
    """The answer is a function of the analysis' own code (the caller's module and what it imports
    to analyze): changing it analyzes again."""
    write(modules, "result_key_job", "def job():\n    return 1\n")
    code = write(
        modules,
        "result_key_analysis",
        "import sys\nRUNS = []\n\ndef analyze():\n"
        "    from doeff_effect_analyzer import _program_effects\n"
        "    RUNS.append(1)\n"
        "    _program_effects.module_ast(sys.modules['result_key_job'])\n"
        "    return ('answer',)\n",
    )
    importlib.import_module("result_key_job")
    analysis_module = importlib.import_module("result_key_analysis")
    rc.cached_result(IDENTITY, analysis_module.analyze)
    rc.cached_result(IDENTITY, analysis_module.analyze)
    assert len(analysis_module.RUNS) == 1
    code.write_text(code.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
    rc.cached_result(IDENTITY, analysis_module.analyze)
    assert len(analysis_module.RUNS) == 2


def test_a_changed_macro_of_a_read_hy_module_analyzes_again(cache_dir: Path, modules: Path) -> None:
    """A Hy module's values are built by the macros it requires: their module is in the key."""
    import hy  # noqa: F401 - registers the .hy import hook

    macros = modules / "result_key_macros.hy"
    macros.write_text("(defmacro answer [] 1)\n", encoding="utf-8")
    (modules / "result_key_hy_job.hy").write_text(
        "(require result_key_macros [answer])\n(defn job [] (answer))\n", encoding="utf-8"
    )
    importlib.import_module("result_key_hy_job")
    analysis = Counting("result_key_hy_job")
    rc.cached_result(IDENTITY, analysis)
    macros.write_text("(defmacro answer [] 2)\n", encoding="utf-8")
    rc.cached_result(IDENTITY, analysis)
    assert analysis.runs == 2


def test_a_changed_reader_macro_of_a_read_hy_module_analyzes_again(cache_dir: Path, modules: Path) -> None:
    """A Hy module's values are built by the reader macros it requires too (Hy keeps them in a table of
    their own): their module is in the key. The names of Hy's macro tables are read from the one home
    of Hy's internal names (``doeff_hy_bytecode_guard._records``); this and the test above measure that
    the key follows both tables of the Hy that uv.lock pins (card ki-ffe72a1fb7d7 — they are selected
    by a change of uv.lock, with packages/doeff-hy/tests/test_bytecode_guard.py)."""
    import hy  # noqa: F401 - registers the .hy import hook

    readers = modules / "result_key_readers.hy"
    readers.write_text("(defreader twice (setv x (.parse-one-form &reader)) `(* 2 ~x))\n", encoding="utf-8")
    (modules / "result_key_reader_job.hy").write_text(
        "(require result_key_readers :readers [twice])\n(defn job [] #twice 5)\n", encoding="utf-8"
    )
    importlib.import_module("result_key_reader_job")
    analysis = Counting("result_key_reader_job")
    rc.cached_result(IDENTITY, analysis)
    readers.write_text("(defreader twice (setv x (.parse-one-form &reader)) `(* 3 ~x))\n", encoding="utf-8")
    rc.cached_result(IDENTITY, analysis)
    assert analysis.runs == 2
