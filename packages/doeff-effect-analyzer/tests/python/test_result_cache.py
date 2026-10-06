"""An analysis answer is reused from disk only while every module file it was computed under has the same content.

The answer of a closure analysis depends on the sources it reads and on the runtime values importing
them made, so it is keyed on the files of all modules loaded when it was computed (agora-redesign
#1645). One changed byte in any of them must make the next call analyze again. The key is the
files' content, not their path (agora-redesign #3777): another checkout with the same content reads
the stored answer, and checkouts with other contents keep their answers side by side.
"""

import contextlib
import functools
import importlib
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from doeff_effect_analyzer import program_effects as pe
from doeff_effect_analyzer import result_cache as rc


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
    """An analysis that counts how often it really runs."""

    def __init__(self) -> None:
        self.runs = 0

    def __call__(self) -> tuple[str, ...]:
        self.runs += 1
        return ("gap ← somewhere",)


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
        assert rc.cached_result(IDENTITY, lambda: ("from a",)) == ("from a",)
        load_from(second, name, monkeypatch)
        assert rc.cached_result(IDENTITY, lambda: ("from b",)) == ("from a",)
        monkeypatch.setattr(rc, "_digest", no_reading)
        assert rc.cached_result(IDENTITY, lambda: ("again",)) == ("from a",)
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
        assert rc.cached_result(IDENTITY, lambda: ("from a",)) == ("from a",)
        load_from(second, name, monkeypatch)
        assert rc.cached_result(IDENTITY, lambda: ("from b",)) == ("from b",)
        load_from(first, name, monkeypatch)
        assert rc.cached_result(IDENTITY, lambda: ("again in a",)) == ("from a",)
        load_from(second, name, monkeypatch)
        assert rc.cached_result(IDENTITY, lambda: ("again in b",)) == ("from b",)
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
        analysis = Counting()
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
        assert rc.cached_result(IDENTITY, lambda: ("from a",)) == ("from a",)
        sys.modules.pop(name)
        sys.path.remove(str(checkouts["a"]))
        monkeypatch.syspath_prepend(str(checkouts["b"]))
        importlib.import_module(name)
        assert rc.cached_result(IDENTITY, lambda: ("from b",)) == ("from b",)
        # The same checkout still reads its stored answer.
        assert rc.cached_result(IDENTITY, lambda: ("again",)) == ("from b",)
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
