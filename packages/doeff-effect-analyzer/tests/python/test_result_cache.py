"""An analysis answer is reused from disk only while every module file it was computed under is unchanged.

The answer of a closure analysis depends on the sources it reads and on the runtime values importing
them made, so it is keyed on the files of all modules loaded when it was computed (agora-redesign
#1645). One changed byte in any of them must make the next call analyze again.
"""

import contextlib
import functools
import importlib
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
